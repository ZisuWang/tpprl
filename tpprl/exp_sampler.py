"""This file contains the samplers implemented using numpy and RedQueen
compatible broadcasters (both single-threaded and multi-threaded versions)."""
import decorated_options as Deco
import numpy as np
import redqueen.opt_model as OM


class CDFSampler:
    """A generic sampler which assumes that the intensity u(t) has the form:

    f(vt * hi + bt + wt * (t - t0)).

    The function 'f' is left unimplemented and has to be implemented by sub-classes.

    Notationally, c = vt * hi + bt.
    """

    @Deco.optioned()
    def __init__(self, vt, wt, bt, init_h, t_min, seed=42):
        self.seed = seed
        self.vt = np.asarray(vt).squeeze()
        self.wt = np.asarray(wt).squeeze()
        self.bt = np.asarray(bt).squeeze()

        self.w = np.asarray(wt).squeeze()
        self.Q = 1.0

        self.random_state = np.random.RandomState(seed)
        self.reset(t_min, init_h, reset_sample=True)

    def cdf(self, t):
        """Return the CDF calculated at 't', given the current state of the intensity.
        It also assumes that the last event was at self.t0.
        """
        raise NotImplementedError('cdf has to be implemented by the sub-class')

    def generate_sample(self):
        """Find a sample from the Exp process."""
        raise NotImplementedError('generate_sample has to be implemented by the sub-class.')

    def reset_only_sample(self, cur_time):
        """Resets only the present sample.

        This allows generating multiple samples (by updating t0) from the
        intensity one after the other without registering any new events."""

        self.c = self.c + (self.w * (cur_time - self.t0))
        self.t0 = cur_time
        self.u_unif = self.random_state.rand()
        self.Q = 1.0

        return self.generate_sample()

    def reset(self, cur_time, init_h, reset_sample):
        """Reset the sampler for generating another event."""

        if reset_sample:
            self.u_unif = self.random_state.rand()
            self.Q = 1.0
        else:
            self.Q *= (1 - self.cdf(cur_time))

        self.h = init_h
        self.c = np.squeeze(self.vt.dot(self.h) + self.bt)
        self.t0 = cur_time

        return self.generate_sample()

    def register_event(self, time, new_h, own_event):
        """Saves the event and generated a new time for the next event."""
        return self.reset(time, new_h, reset_sample=own_event)

    def get_last_hidden_state(self):
        return self.h

    def get_last_c(self):
        return self.c

    def int_u(self, dt, c):
        """Value of U(dt) - U(0)."""
        raise NotImplementedError('int_u needs to be implemented by the sub-class.')

    def log_u(self, t, c):
        """Value of log u(t)."""
        raise NotImplementedError('Needs to be implemented by the sub-class.')

    def int_u_2(self, t, c):
        """Value of U^2(dt) - U^2(0)."""
        raise NotImplementedError('Needs to be implemented by the sub-class.')

    def calc_quad_loss(self, event_time_deltas, c_is):
        """Calculates the regularise loss.
        The last entry of event_time_deltas should be T - t_last.
        The first entry of hidden_states should be the initial state.
        """
        return sum(self.int_u_2(dt, c)
                   for dt, c in zip(event_time_deltas, c_is))

    def calc_LL(self, event_time_deltas, c_is, is_own_event):
        """Calculates the log-likelihood.
        The last entry of event_time_deltas should be T - t_last.
        The first entry of hidden_states should be the initial state.
        The last entry of is_own_event correspond to the phantom event at the end of the survival.
        """
        assert not is_own_event[-1], "The last entry cannot be an event."

        LL_log = sum(self.log_u(dt, c)
                     for dt, c, o in zip(event_time_deltas, c_is, is_own_event)
                     if o)
        LL_int = sum(self.int_u(dt, c) for dt, c in zip(event_time_deltas, c_is))

        return LL_log - LL_int


class ExpCDFSampler(CDFSampler):
    """This is an exponential sampler."""

    def cdf(self, t):
        """Calculates the CDF assuming that the last event was at self.t0"""
        return 1 - np.exp((np.exp(self.c) / self.w) * (1 - np.exp(self.w * (t - self.t0))))

    def generate_sample(self):
        """Find a sample from the Exp process."""
        # Have the uniform sample already drawn
        D = 1 - (self.w / np.exp(self.c)) * np.log((1 - self.u_unif) / self.Q)
        if D <= 0:
            # This is the probability that no event ever happens
            return np.inf
        else:
            return self.t0 + (1 / self.w) * np.log(D)

    def int_u(self, dt, c):
        return (1 / self.wt) * (np.exp(c + self.wt * dt) - np.exp(c))

    def log_u(self, dt, c):
        return c + self.wt * dt

    def int_u_2(self, dt, c):
        return (1 / (2 * self.wt)) * (np.exp(2 * c + 2 * self.wt * dt) -
                                      np.exp(2 * c))


class SigmoidCDFSampler(CDFSampler):
    """This is an sigmoidal intensity sampler.

    Additionally, it assumes that the sigmoid is multiplied by 'k' to scale the intensity.
    """

    @Deco.optioned()
    def __init__(self, vt, wt, bt, init_h, t_min, seed=42, k=1.0):
        self.k = k
        super().__init__(vt, wt, bt, init_h, t_min, seed=seed)

    def cdf(self, t):
        C = (1 + np.exp(self.c)) / (1 + np.exp(self.c + self.wt * (t - self.t0)))
        return 1 - C ** (self.k / self.wt)

    def generate_sample(self):
        D = (1 + np.exp(self.c)) * ((1 - self.u_unif) / self.Q) ** (- self.k / self.wt) - 1
        # print('D = ', D)
        if D <= 0:
            # This is the case when no event ever happens.
            return np.inf
        else:
            return self.t0 + (np.log(D) - self.c) / self.wt

    def log_u(self, dt, c):
        return np.log(1 / (1 + np.exp(-(c + self.wt * dt))))

    def int_u(self, dt, c):
        return (self.k / self.wt) * (np.log1p(np.exp(c + self.wt * dt)) - np.log1p(np.exp(c)))

    def int_u_2(self, dt, c):
        return ((self.k ** 2) / self.wt) * (1 / (1 + np.exp(c + self.wt * dt)) +
                                            np.log1p(np.exp(c + self.wt * dt)) -
                                            1 / (1 + np.exp(c)) -
                                            np.log1p(np.exp(c)))


class ExpRecurrentBroadcasterMP(OM.Broadcaster):
    """This is a broadcaster which follows the intensity function as defined by
    RMTPP paper and updates the hidden state upon receiving each event.

    TODO: The problem is that calculation of the gradient and the loss/LL
    becomes too complicated with numerical stability issues very quickly. Need
    to implement adaptive scaling to handle that issue.

    Also, this embeds the event history implicitly and the state function does
    not explicitly model the loss function J(.) faithfully. This is an issue
    with the theory.
    """

    @Deco.optioned()
    def __init__(self, src_id, seed, t_min,
                 Wm, Wh, Wr, Wt, Bh, sim_opts,
                 wt, vt, bt, init_h, src_embed_map):
        super(ExpRecurrentBroadcasterMP, self).__init__(src_id, seed)
        self.sink_ids = sim_opts.sink_ids
        self.init = False

        # Used to create h_next
        self.Wm = Wm
        self.Wh = Wh
        self.Wr = Wr
        self.Wt = Wt
        self.Bh = Bh
        self.cur_h = init_h
        self.src_embed_map = src_embed_map

        # Needed for the sampler
        self.params = Deco.Options(**{
            'wt': wt,
            'vt': vt,
            'bt': bt,
            'init_h': init_h
        })

        self.exp_sampler = ExpCDFSampler(_opts=self.params,
                                         t_min=t_min,
                                         seed=seed + 1)

    def update_hidden_state(self, src_id, time_delta):
        """Returns the hidden state after a post by src_id and time delta."""
        # Best done using self.sess.run here.
        r_t = self.state.get_wall_rank(self.src_id, self.sink_ids, dict_form=False)
        return np.tanh(
            self.Wm[self.src_embed_map[src_id], :][:, np.newaxis] +
            self.Wh.dot(self.cur_h) +
            self.Wr * np.asarray([np.mean(r_t)]).reshape(-1) +
            self.Wt * time_delta +
            self.Bh
        )

    def get_next_interval(self, event):
        if not self.init:
            self.init = True
            self.state.set_track_src_id(self.src_id, self.sink_ids)
            # Nothing special to do for the first event.

        self.state.apply_event(event)

        if event is None:
            # This is the first event. Post immediately to join the party?
            # Or hold off?
            # Currently, it is waiting.
            return self.exp_sampler.generate_sample()
        else:
            self.cur_h = self.update_hidden_state(event.src_id, event.time_delta)
            next_post_time = self.exp_sampler.register_event(
                event.cur_time,
                self.cur_h,
                own_event=event.src_id == self.src_id
            )
            next_delta = next_post_time - self.last_self_event_time
            # print(next_delta)
            assert next_delta >= 0
            return next_delta


class ExpRecurrentBroadcaster(OM.Broadcaster):
    """This is a broadcaster which follows the intensity function as defined by
    RMTPP paper and updates the hidden state upon receiving each event.

    TODO: The problem is that calculation of the gradient and the loss/LL
    becomes too complicated with numerical stability issues very quickly. Need
    to implement adaptive scaling to handle that issue.

    Also, this embeds the event history implicitly and the state function does
    not explicitly model the loss function J(.) faithfully. This is an issue
    with the theory.
    """

    @Deco.optioned()
    def __init__(self, src_id, seed, trainer, t_min=0):
        super(ExpRecurrentBroadcaster, self).__init__(src_id, seed)
        self.init = False

        self.trainer = trainer

        self.params = Deco.Options(**self.trainer.sess.run({
            # 'Wm': trainer.tf_Wm,
            # 'Wh': trainer.tf_Wh,
            # 'Bh': trainer.tf_Bh,
            # 'Wt': trainer.tf_Wt,
            # 'Wr': trainer.tf_Wr,

            'wt': trainer.tf_wt,
            'vt': trainer.tf_vt,
            'bt': trainer.tf_bt,
            'init_h': trainer.tf_h
        }))

        self.cur_h = self.params.init_h

        self.exp_sampler = ExpCDFSampler(_opts=self.params,
                                         t_min=t_min,
                                         seed=seed + 1)

    def update_hidden_state(self, src_id, time_delta):
        """Returns the hidden state after a post by src_id and time delta."""
        # Best done using self.sess.run here.
        r_t = self.state.get_wall_rank(self.src_id, self.sink_ids, dict_form=False)

        feed_dict = {
            self.trainer.tf_b_idx: np.asarray([self.trainer.src_embed_map[src_id]]),
            self.trainer.tf_t_delta: np.asarray([time_delta]).reshape(-1),
            self.trainer.tf_h: self.cur_h,
            self.trainer.tf_rank: np.asarray([np.mean(r_t)]).reshape(-1)
        }
        return self.trainer.sess.run(self.trainer.tf_h_next,
                                     feed_dict=feed_dict)

    def get_next_interval(self, event):
        if not self.init:
            self.init = True
            self.state.set_track_src_id(self.src_id,
                                        self.trainer.sim_opts.sink_ids)
            # Nothing special to do for the first event.

        self.state.apply_event(event)

        if event is None:
            # This is the first event. Post immediately to join the party?
            # Or hold off?
            # Currently, it is waiting.
            return self.exp_sampler.generate_sample()
        else:
            self.cur_h = self.update_hidden_state(event.src_id, event.time_delta)
            next_post_time = self.exp_sampler.register_event(
                event.cur_time,
                self.cur_h,
                own_event=event.src_id == self.src_id
            )
            next_delta = next_post_time - self.last_self_event_time
            # print(next_delta)
            assert next_delta >= 0
            return next_delta


OM.SimOpts.registerSource('ExpRecurrentBroadcaster', ExpRecurrentBroadcaster)
