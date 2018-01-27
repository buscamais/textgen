import logging
import math

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.autograd import Variable
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence

from train.train_helper import ResultPackage
from utils.utils import to_gpu

log = logging.getLogger('main')


class Encoder(nn.Module):
    def __init__(self, cfg, vocab):
        super(Encoder, self).__init__()
        self.cfg = cfg
        self.vocab = vocab
        self.noise_radius = cfg.noise_radius

        # Vocabulary embedding
        self.embed = nn.Embedding(cfg.vocab_size, cfg.embed_size)
        self.embed.weight.data.copy_(torch.from_numpy(vocab.embed_mat))

        if cfg.load_glove and cfg.fix_embed:
            self.embed.weight.requires_grad = False

        # RNN Encoder and Decoder
        self.encoder = nn.LSTM(input_size=cfg.embed_size,
                               hidden_size=cfg.hidden_size,
                               num_layers=cfg.nlayers,
                               dropout=cfg.dropout,
                               batch_first=True)

        self._init_weights()

    def _init_weights(self):
        # unifrom initialization in the range of [-0.1, 0.1]
        initrange = 0.1

        # Initialize Vocabulary Matrix Weight
        if not self.cfg.load_glove:
            self.embed.weight.data.uniform_(-initrange, initrange)
        # by default it's initialized with normal_(0,1)

        # Initialize Encoder and Decoder Weights
        for p in self.encoder.parameters():
            p.data.uniform_(-initrange, initrange)

    def store_grad_norm(self, grad):
        norm = torch.norm(grad, 2, 1)
        self.grad_norm = norm.detach().data.mean()
        # use this when compute disc_c's gradient (register_hook)
        return grad

    def forward(self, indices, lengths, noise, save_grad_norm=False):
        batch_size, maxlen = indices.size()

        hidden = self._encode(indices, lengths, noise)

        if save_grad_norm and hidden.requires_grad:
            hidden.register_hook(self.store_grad_norm)

        return hidden

    def _encode(self, indices, lengths, noise):
        # indices.size() : batch_size x max(lengths) [Variable]
        # len(lengths) : batch_size [List]
        embeddings = self.embed(indices)
        # embeddings.data.size() : batch_size x max(lenghts) x embed_dim [Variable]
        packed_embeddings = pack_padded_sequence(input=embeddings,
                                                 lengths=lengths,
                                                 batch_first=True)
        # Encode
        packed_output, state = self.encoder(packed_embeddings)
        hidden, cell = state # last states (tuple the length of 2)

        hidden = hidden[-1]  # get hidden state of last layer of encoder
        norms = torch.norm(hidden, 2, 1)

        hidden = torch.div(hidden, norms.unsqueeze(1).expand_as(hidden))

        if noise and self.cfg.noise_radius > 0:
            gauss_noise = torch.normal(means=torch.zeros(hidden.size()),
                                       std=self.noise_radius)
            hidden = hidden + to_gpu(self.cfg.cuda, Variable(gauss_noise))
            #log.debug("Encoder gradient norm has been saved.")

        return hidden # batch_size x hidden_size
