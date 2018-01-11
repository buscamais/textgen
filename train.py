import logging
import numpy as np
import time

from tensorboardX import SummaryWriter
import torch
import torch.nn as nn
from torch.autograd import Variable

from train_helper import TrainingSupervisor
from utils import set_random_seed, to_gpu

from autoencoder import Autoencoder
from code_disc import CodeDiscriminator
from generator import Generator
from sample_disc import SampleDiscriminator

log = logging.getLogger('main')


def print_line(char='-', row=1, length=130):
    for i in range(row):
        log.info(char * length)

def print_ae_nums(cfg, epoch, batch, nbatch, loss, accuracy):
    print_line(' ')
    print_line()
    log.info("| Epoch %d/%d | Batches %d/%d | Loss %5.2f | Acc %8.2f |"
             % (epoch, cfg.epochs, batch, nbatch,
                loss, accuracy))

def ids_to_sent(vocab, ids, no_pad=True):
    if no_pad:
        return " ".join([vocab.idx2word[idx] for idx in ids
                         if idx is not vocab.PAD_ID])
    else:
        return " ".join([vocab.idx2word[idx] for idx in ids])

def print_ae_sents(vocab, target_ids, output_ids, nline=5):
    print_line()
    for i, (tar_ids, out_ids) in enumerate(zip(target_ids, output_ids)):
        if i > nline - 1: break
        log.info("[X] " + ids_to_sent(vocab, tar_ids))
        log.info("[Y] " + ids_to_sent(vocab, out_ids))
        print_line()

def print_gan_nums(cfg, epoch, batch, nbatch, loss_d, loss_d_real,
                   loss_d_fake, loss_g):
    print_line()
    log.info("| Epoch %d/%d | Batches %d/%d | Loss_D %.8f | Loss_D_real "
             "%.8f | Loss_D_fake %.8f | Loss_G %.8f"
             % (epoch, cfg.epochs, batch, nbatch,
                loss_d, loss_d_real, loss_d_fake, loss_g))

def print_gen_sents(vocab, output_ids, nline=999):
    print_line()
    for i, ids in enumerate(output_ids):
        if i > nline - 1: break
        log.info(ids_to_sent(vocab, ids))
        print_line()
    print_line(' ')

def align_word_attn(words, attn_list, min_width=4):
    # attn_list[i] : [attn1[i], attn2[i], attn3[i]]
    word_formats = ' '.join(['{:^%ds}' % max(min_width, len(word))
                            for word in words])
    word_str = word_formats.format(*words)
    attn_str_list = []
    for attn in attn_list:
        attn_formats = ' '.join(['{:^%d}' % max(min_width, len(word))
                                 for word in words])
        attn = [int(a*100) for a in attn]
        attn_str = attn_formats.format(*attn)
        attn_str = attn_str.replace('-100', '    ') # remove empty slots
        attn_str_list.append(attn_str)
    return word_str, attn_str_list

def mark_empty_attn(attns, max_len):
    filter_n_stride = [(3,1), (3,2), (3,2)]
    assert len(filter_n_stride) == len(attns)
    filters, strides = zip(*filter_n_stride)
    stride_ = 1
    actual_strides = []
    for stride in strides:
        stride_ *= stride
        actual_strides.append(stride_) # 1, 2, 4
    left_empty = 0
    actual_stride = 1
    new_attns = []
    for i, attn in enumerate(attns):
        # layer level
        if i == 0:
            prev_stride = 1
        else:
            prev_stride = strides[i-1]
        left_empty += (filters[i] // 2) * prev_stride
        new_attn = np.ones([attn.shape[0], left_empty]) * (-1)
        empty_attn = np.ones([attn.shape[0], 1]) * (-1) # for column inserting
        attn_cnt = 0
        actual_strides *= strides[i]
        for j in range(max_len - left_empty):
            #import ipdb; ipdb.set_trace()
            if j % actual_strides[i]  == 0 and attn_cnt < attn.shape[1]:
                new_attn = np.append(new_attn, attn[:, [attn_cnt]], axis=1)
                attn_cnt += 1
            else:
                new_attn = np.append(new_attn, empty_attn, axis=1)
        new_attns.append(new_attn)
        # [array(attn_1), array(att_2), array(attn_3)]
    return list(zip(*new_attns))
    # [[array(attn_1)[0], array(att_2)[0], array(attn_3)[0]],
    #  [array(attn_1)[1], array(att_2)[1], array(attn_3)[1]],
    #                        ......... (batch_size)        ]

def print_attn_weights(cfg, vocab, real_ids, fake_ids,
                       real_attns, fake_attns, nline):
    real_attns = mark_empty_attn(real_attns, cfg.max_len)
    fake_attns = mark_empty_attn(fake_attns, cfg.max_len)
    # len(real_attns) : batch_size
    # real_attns[0] : [array(attn_1[0]), array(attn_2[0], array(attn_3[0]))
    def print_aligned(batch_ids, batch_attns):
        #import ipdb; ipdb.set_trace()
        for i, (ids, attn_list) in enumerate(zip(batch_ids, batch_attns)):
            if i > nline - 1: break
            words = [vocab.idx2word[idx] for idx in ids]
            word_str, attn_str_list = align_word_attn(words, attn_list)
            for attn_str in reversed(attn_str_list): # from topmost attn layer
                log.info(attn_str)
            log.info(word_str)
            print_line()

    print_line()
    log.info('Attention on real samples')
    print_line()
    print_aligned(real_ids, real_attns)
    log.info('Attention on fake samples')
    print_line()
    print_aligned(fake_ids, fake_attns)
    print_line(' ')


def train(net):
    log.info("Training start!")
    cfg = net.cfg # for brevity
    set_random_seed(cfg)
    fixed_noise = Generator.make_noise(cfg, 3) # for generator
    writer = SummaryWriter(self.log_dir)
    sv = TrainingSupervisor(net)

    while not sv.global_stop():
        while not sv.epoch_stop():

            # train autoencoder ----------------------------
            for i in range(cfg.niters_ae): # default: 1 (constant)
                batch = net.data_ae.next_or_none()
                if net.data_ae.batch is None:
                    break  # end of epoch3

                ae_loss, ae_acc = Autoencoder.train_(cfg, net.ae, batch)
                net.optim_ae.step()
                sv.inc_batch_step()

            # train gan ----------------------------------
            for k in range(sv.gan_niter): # epc0=1, epc2=2, epc4=3, epc6=4

                # train discriminator/critic (at a ratio of 5:1)
                for i in range(cfg.niters_gan_d): # default: 5
                    # feed a seen sample within this epoch; good for early training
                    # randomly select single batch among entire batches in the epoch
                    batch = net.data_gan.next()

                    real_code = Autoencoder.encode_(cfg, net.ae, batch)
                    fake_code = Generator.generate_(cfg, net.gen, False)
                    errs_d_c= CodeDiscriminator.train_(
                        cfg, net.disc_c, net.ae, real_code, fake_code)

                    real_ids, real_states = \
                        Autoencoder.decode_(cfg, net.ae, real_code, False)
                    fake_ids, fake_states = \
                        Autoencoder.decode_(cfg, net.ae, fake_code, False)

                    if cfg.with_attn:
                        errs_d_s, attns = SampleDiscriminator.train_(
                            cfg, net.disc_s, real_states, fake_states)

                    net.optim_disc_c.step()
                    if cfg.with_attn:
                        net.optim_disc_s.step()
                    net.optim_ae.step()

                # train generator
                for i in range(cfg.niters_gan_g): # default: 1
                    disc_s = net.disc_s if cfg.with_attn else None
                    err_g, err_g_c, err_g_s = Generator.train_(
                        cfg, net.gen, net.ae, net.disc_c, None) # NOTE: disc_s
                    net.optim_gen.step()

            # it can be different from niter when niters_ae is larger than 1
            if not sv.batch_step % cfg.log_interval == 0:
                continue

            # exponentially decaying noise on autoencoder
            # noise_raius = 0.2(default)
            # noise_anneal = 0.995(default)
            net.ae.noise_radius = net.ae.noise_radius * cfg.noise_anneal

            epoch = sv.epoch_step
            nbatch = sv.batch_step
            niter = sv.global_step

            # Autoencoder
            targets, outputs = Autoencoder.eval_(cfg, net.ae, batch)
            print_ae_nums(cfg, epoch, nbatch, net.nbatch, ae_loss, ae_acc)
            print_ae_sents(net.vocab, targets, outputs, 3)

            # Generator + Discriminator_c
            print_gan_nums(cfg, epoch, nbatch, net.nbatch, *errs_d_c, err_g_c)
            outputs = Generator.eval_(cfg, net.gen, net.ae, fixed_noise)
            print_gen_sents(net.vocab, outputs)

            err_g_s = 0 # NOTE
            # Discriminator_s
            if cfg.with_attn:
                print_gan_nums(cfg, epoch, nbatch, net.nbatch,
                               *errs_d_s, err_g_s)
                print_attn_weights(cfg, net.vocab, real_ids, fake_ids,
                                   *attns, 3)

            # Autoencoder
            writer.add_scalar('data/ae_loss', ae_loss, niter)
            writer.add_scalar('data/ae_accuracy',  ae_acc, niter)

            # Generator
            writer.add_scalar('data/gen_loss', err_g, niter)
            writer.add_scalar('data/gen_c_loss', err_g_c, niter)
            if cfg.with_attn:
                writer.add_scalar('data/gen_s_loss', err_g_s, niter)

            # Discriminator_c
            err_d, err_d_real, err_d_fake = errs_d_c
            writer.add_scalar('data/disc_c_loss', err_d, niter)
            writer.add_scalar('data/disc_c_real_loss', err_d_real, niter)
            writer.add_scalar('data/disc_c_fake_loss', err_d_fake, niter)

            # Discriminator_s
            if cfg.with_attn:
                err_d, err_d_real, err_d_fake = errs_d_s
                writer.add_scalar('data/disc_s_loss', err_d, niter)
                writer.add_scalar('data/disc_s_real', err_d_real, niter)
                writer.add_scalar('data/disc_s_fake', err_d_fake, niter)

            sv.save()

        # end of epoch ----------------------------
        sv.inc_epoch_step()
