import logging
import torch
import torch.nn as nn
from torch.autograd import Variable
import torch.nn.functional as F
from torch.nn.utils.rnn import pack_padded_sequence

from models.base_module import BaseModule, BaseEncoder
from nn.embedding import WordEmbedding
from utils.utils import to_gpu

log = logging.getLogger('main')


class EncoderRNN(BaseEncoder):
    def __init__(self, cfg):
        super(EncoderRNN, self).__init__(cfg)

        # RNN Encoder and Decoder
        self.encoder = nn.LSTM(input_size=cfg.word_embed_size,
                               hidden_size=cfg.hidden_size,
                               num_layers=cfg.nlayers,
                               dropout=cfg.dropout,
                               batch_first=True)
        self._init_weights()

    def _encode(self, embed_in, lengths):
         # indices = [bsz, max_len], lengths = [bsz]

        # Embedding and pack
        packed_embeddings = pack_padded_sequence(input=embed_in,
                                                 lengths=lengths,
                                                 batch_first=True)
        # RNN encoder
        packed_output, state = self.encoder(packed_embeddings)
        hidden, cell = state # last states (tuple the length of 2)
        code = hidden[-1]  # get hidden state of last layer of encoder

        return code # batch_size x hidden_size

    def _init_weights(self):
        # Unifrom initialization in the range of [-0.1, 0.1]
        initrange = 0.1

        # Initialize Encoder and Decoder Weights
        for p in self.encoder.parameters():
            p.data.uniform_(-initrange, initrange)


class EncoderCNN(BaseEncoder):
    def __init__(self, cfg):
        super(EncoderCNN, self).__init__(cfg)
        # Expected input dim
        #   : [bsz, c(embed or hidden size), h(1), w(max_len)]
        arch = cfg.arch_cnn

        # Convoutional layers
        self.convs = []
        for i in range(arch.n_conv):
            conv = nn.Conv2d(arch.c[i], arch.c[i+1], (1, arch.f[i]), arch.s[i])
            self.convs.append(conv)
            self.add_module("Conv(%d)" % (i+1), conv)

        self.criterion_mse = nn.MSELoss()

    def _encode(self, embed_in):
        # NOTE : lengths can be used for pad masking
        if embed_in.size(1) < self.cfg.max_len:
            embed_in = self._append_zero_embeds(embed_in)
        elif embed_in.size(1) > self.cfg.max_len:
            embed_in = embed_in[:, :self.cfg.max_len, :]

        # [bsz, word_embed_size, 1, max_len]
        x = x_in = embed_in.permute(0, 2, 1).unsqueeze(2)

        for i, conv in enumerate(self.convs):
            if i < len(self.convs) - 1:
                x = F.relu(conv(x))
            else:
                x = conv(x)

        code = x.squeeze()
        assert(len(code.size()) == 2)

        return code # [bsz, hidden_size]

    def _append_zero_embeds(self, tensor):
        bsz, lengths, embed_size = tensor.size()
        pad_len = (self.cfg.max_len) - lengths
        if pad_len > 0:
            pads = torch.zeros([bsz, pad_len, embed_size])
            pads = Variable(pads, requires_grad=False).cuda()
            return torch.cat((tensor, pads), dim=1)
        else:
            return tensor


class CodeSmoothingRegularizer(BaseModule):
    def __init__(self, cfg):
        super(CodeSmoothingRegularizer, self).__init__()
        self.cfg = cfg
        self.is_with_var = True  # default
        self.fc_logvar = nn.Linear(cfg.hidden_size, cfg.hidden_size)
        self._var = None

    @property
    def var(self):
        if self._var is not None:
            return self._var
        else:
            return 0

    def with_var(self, code):
        self.is_with_var = True  # default
        return self(code)

    def without_var(self, code):
        self.is_with_var = False
        return self(code)

    def forward(self, code):
        logvar = self.fc_logvar(code)
        code_new = self._reparameterize(code, logvar)
        self.is_with_var = True # back to default
        return code_new

    def _reparameterize(self, code, logvar):
        if self.is_with_var:
            std = logvar.mul(0.5).exp_()
            eps = Variable(std.data.new(std.size()).normal_())
            var = eps.mul(std)
            self._var = var.mean().data[0]
            return var.add_(code)
        else:
            self._var = None
            return code
