import abc
import logging
import math

import torch
from torch.autograd import Variable
import torch.nn as nn
import torch.nn.functional as F

from utils.utils import to_gpu

log = logging.getLogger('main')


class BaseModule(nn.Module):
    def __init__(self):
        super(BaseModule, self).__init__()

    @property
    def trainer(self, zero_grad=True):
        if zero_grad:
            self.zero_grad()
        return self.train(True)

    @property
    def tester(self, zero_grad=True):
        if zero_grad:
            self.zero_grad()
        return self.train(False)

    def forward(self, *input):
        raise NotImplementedError

    def _normalize(self, code, p=2, dim=1):
        return F.normalize(code, p, dim)


class BaseAutoencoder(BaseModule):
    def __init__(self):
        super(BaseAutoencoder, self).__init__()
        self.cfg = None # placeholder

    def forward(self, *input):
        raise NotImplementedError

    def clip_grad_norm(self):
        nn.utils.clip_grad_norm(self.parameters(), self.cfg.clip)
        return self


class BaseEncoder(BaseAutoencoder):
    def __init__(self, cfg):
        super(BaseEncoder, self).__init__()
        self.cfg = cfg
        self.noise_radius = cfg.noise_radius
        self._is_add_noise = False

        last_size = cfg.hidden_size_w + cfg.hidden_size_t
        self.code_t = nn.Linear(cfg.hidden_size_w + cfg.hidden_size_t,
                                cfg.hidden_size_t)
        self.code_w = nn.Linear(cfg.hidden_size_w + cfg.hidden_size_t,
                                cfg.hidden_size_w)

    def with_noise(self, *inputs):
        self._is_add_noise = True
        return self.__call__(*inputs)

    def forward(self, *inputs):
        code = self._encode(*inputs)

        code_t = self.code_t(code)
        code_w = self.code_w(code)

        # normalization
        if self.cfg.code_norm:
            code_t = self._normalize(code_t)
            code_w = self._normalize(code_w)

        # unit gaussian noise
        if self._is_add_noise and self.noise_radius > 0:
            code_t = self._add_gaussian_noise_to(code_t)
            code_w = self._add_gaussian_noise_to(code_w)
            self._is_add_noise = False # back to default

        return code_t, code_w

    def _add_gaussian_noise_to(self, code):
        # gaussian noise
        noise = torch.normal(means=torch.zeros(code.size()),
                             std=self.noise_radius)
        noise = to_gpu(self.cfg.cuda, Variable(noise))
        return code + noise

    def decay_noise_radius(self):
        self.noise_radius = self.noise_radius * self.cfg.noise_anneal


class BaseDecoder(BaseAutoencoder):
    def __init__(self):
        super(BaseDecoder, self).__init__()
