import torch
import torch.nn as nn
from torch.autograd import Variable

from models.base_module import BaseModule
from utils.writer import ResultWriter
from utils.utils import to_gpu


class BaseGenerator(BaseModule):
    def __init__(self, cfg):
        super(BaseGenerator, self).__init__()
        self.cfg = cfg

    def stack_layers(self, ninput, noutput):
        activation = nn.ReLU()
        layer_sizes = [ninput] + [int(x) for x in self.cfg.arch_g.split('-')]
        layers = []

        # add_module here is required to define each layer with different name
        #    in the interative loop automatically according to desired architecture
        # By doing so, init & forward iterative codes can be made shorter
        for i in range(len(layer_sizes)-1):
            layer = nn.Linear(layer_sizes[i], layer_sizes[i+1])
            layers.append(layer)
            self.add_module("layer"+str(i+1), layer)

            bn = nn.BatchNorm1d(layer_sizes[i+1], eps=1e-05, momentum=0.1)
            layers.append(bn)
            self.add_module("bn"+str(i+1), bn)

            layers.append(activation)
            self.add_module("activation"+str(i+1), activation)

        # last linear layer
        layer = nn.Linear(layer_sizes[-1], noutput)
        layers.append(layer)
        self.add_module("layer"+str(len(layer_sizes)), layer) # bug fix

        # last activation
        # layer = nn.Tanh()
        # layers.append(layer)
        # self.add_module("activation"+str(len(layer_sizes)), layer)

        # Generator(
        #     (layer1): Linear(in_features=z_size, out_features=300)
        #     (bn1): BatchNorm1d(300, eps=1e-05, momentum=0.1, affine=True)
        #     (activation1): ReLU()
        #     (layer2): Linear(in_features=300, out_features=300)
        #     (bn2): BatchNorm1d(300, eps=1e-05, momentum=0.1, affine=True)
        #     (activation2): ReLU()
        #     (layer3): Linear(in_features=300, out_features=nhidden)
        # )
        return layers

    def _init_weights(self):
        # Initialization with Gaussian distribution: N(0, 0.02)
        init_std = 0.02
        for layer in self.layers:
            try:
                layer.weight.data.normal_(0, init_std)
                layer.bias.data.fill_(0)
            except:
                pass


class Generator(BaseGenerator):
    def __init__(self, cfg):
        super(Generator, self).__init__(cfg)
        # arguments default values
        #   ninput: args.z_size=100
        #   noutput: args.nhidden=300
        #   layers: arch_d: 300-300

        # z_size(in) --(layer1)-- 300 --(layer2)-- 300 --(layer3)-- nhidden(out)
        self._with_noise = False
        self.layers = self.stack_layers(cfg.z_size, cfg.hidden_size_w)
        self._init_weights()

    def with_noise(self, noise):
        self._with_noise = True
        return self(noise)

    def forward(self, noise):
        assert noise.size(1) == self.cfg.z_size
        x = noise
        for i, layer in enumerate(self.layers):
            x = layer(x)

        if self._with_noise:
            noise = torch.normal(mean=torch.zeros(x.size()), std=0.1)
            noise = to_gpu(self.cfg.cuda, Variable(noise))
            x = x + noise
            with_noise = False
        return x

    def for_train(self):
        return self(self.get_noise(self.cfg.batch_size))

    def for_eval(self):
        return self(self.get_noise(self.cfg.eval_size))

    def get_noise(self, num_samples=None):
        if num_samples is None:
            num_samples = self.cfg.batch_size
        noise = Variable(torch.ones(num_samples, self.cfg.z_size))
        noise = to_gpu(self.cfg.cuda, noise)
        noise.data.normal_(0, 1)
        return noise


class ReversedGenerator(BaseGenerator):
    def __init__(self, cfg):
        super(ReversedGenerator, self).__init__(cfg)
        self.layers = self.stack_layers(cfg.hidden_size_w, cfg.z_size)
        self._init_weights()

    def forward(self, x):
        assert x.size(1) == self.cfg.hidden_size_w
        for i, layer in enumerate(self.layers):
            x = layer(x)
        return x
