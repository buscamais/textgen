import logging
import os
import time
from collections import OrderedDict
from test.evaluate import evaluate_sents

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from models.decoder import DecoderRNN
from torch.autograd import Variable
from train.supervisor import TrainingSupervisor
from train.train_helper import (GradientScalingHook, GradientTransferHook,
                                load_test_data, mask_output_target, SigmaHook)
from test.kenlm import train_kenlm
from utils.utils import set_random_seed, to_gpu
from utils.writer import ResultWriter

log = logging.getLogger('main')
odict = OrderedDict


class Trainer(object):
    def __init__(self, net):
        log.info("Training start!")
        # set_random_seed(net.cfg)
        self.net = net
        self.cfg = net.cfg
        #self.fixed_noise = net.gen.make_noise_size_of(net.cfg.eval_size)

        self.test_sents = load_test_data(net.cfg)
        self.pos_one = to_gpu(net.cfg.cuda, torch.FloatTensor([1]))
        self.neg_one = self.pos_one * (-1)

        self.result = ResultWriter(net.cfg)
        self.sv = TrainingSupervisor(net, self.result)
        #self.sv.interval_func_train.update({net.enc.decay_noise_radius: 200})

        self.enc_h_hook = GradientScalingHook()
        #self.code_var_hook = GradientScalingHook()
        #self.tansfer_hook = GradientTransferHook()
        self.noise = 0.8
        #self.noise = net.cfg.noise_radius

        while not self.sv.is_end_of_training():
            self.train_loop(self.cfg, self.net, self.sv)

    def train_loop(self, cfg, net, sv):
        """Main training loop"""

        with sv.training_context():

            # train autoencoder
            for i in range(sv.niter_ae):  # default: 1 (constant)
                if net.data_ae.step.is_end_of_step():
                    break
                batch = net.data_ae.next()
                self._train_autoencoder(batch)

            # train gan
            for k in range(sv.niter_gan):  # epc0=1, epc2=2, epc4=3, epc6=4

                # train discriminator/critic (at a ratio of 5:1)
                for i in range(cfg.niter_gan_d):  # default: 5
                    batch = net.data_gan.next()
                    #self._train_discriminator(batch)
                    self._train_code_vae(batch)

                # train generator(with disc) / decoder(with disc_s)
                for i in range(cfg.niter_gan_g):  # default: 1
                    #self._train_generator()
                    self._train_dec2(batch)

            #self._train_regularizer(batch)

        if sv.is_evaluation():
            with sv.evaluation_context():
                batch = net.data_eval.next()
                #self._eval_autoencoder(batch, 'tf')
                self._eval_autoencoder(batch)
                self._generate_text()

        if sv.global_step % 5000 == 0:
            self._reverse_ppl(self.net.dec, 'dec1_ppl')
            self._reverse_ppl(self.net.dec2, 'dec2_ppl')

    def _reverse_ppl(self, dec, name='Reversed_PPL'):
        self.net.set_modules_train_mode(True)
        decoded_text = []
        with torch.no_grad():
            # generate 100 x 1000 samples
            for i in range(100):
                noise = self.net.gen.get_noise(1000)
                code_fake = self.net.gen(noise)
                decoded = dec.tester(code_fake,
                                              max_len=self.cfg.max_len)
                decoded_text.append(decoded.get_text_batch())

        decoded_text = np.concatenate(decoded_text, axis=0)
        try:
            ppl = train_kenlm(self.net, decoded_text, self.sv.global_step)
            self.result.add(name, odict(ppl=ppl))
        except:
            log.info("Failed to train kenlm!")

    def _train_autoencoder(self, batch, name='AE_train'):
        self.net.set_modules_train_mode(True)

        # Build graph
        embed = self.net.embed_w(batch.src)
        code = self.net.enc.with_noise(embed, batch.len)
        #code = self.net.reg.with_var(enc_h)
        #cos_sim = F.cosine_similarity(code, code_var, dim=1).mean()
        decoded = self.net.dec(code, batch=batch)
        # tags, words = self.net.dec.free_running(
        #     code_t, code_w, max(batch.len))

        # Register hook
        #enc_h.register_hook(self.enc_h_hook.save_grad_norm)
        #code.register_hook(self.code_hook.save_grad_norm)
        #code_var.register_hook(self.code_var_hook.save_grad_norm)
        # code.register_hook(self.net.enc.save_ae_grad_norm_hook)
        # decoded.embed.register_hook(self.net.dec.save_ae_grad_norm_hook)

        # Compute word prediction loss and accuracy
        #target = batch.src.view(-1)
        loss_recon, acc = self._recon_loss_and_acc_for_rnn(
            decoded.prob, batch.tar, len(self.net.vocab_w))
        #loss_var = 1 / torch.sum(self.net.reg.var) * 0.0000001
        #loss_mean = code_var.mean()
        #loss_var = loss_recon.detach() / loss_var.detach() * loss_var * 0.2
        #loss_kl = self._compute_kl_div_loss(self.net.reg.mu, self.net.reg.sigma)
        loss = loss_recon #+ loss_kl

        loss.backward()

        # to prevent exploding gradient in RNNs
        self.net.embed_w.clip_grad_norm_()
        self.net.enc.clip_grad_norm_()
        #self.net.reg.clip_grad_norm_()
        self.net.dec.clip_grad_norm_()

        # optimize
        self.net.optim_embed_w.step()
        self.net.optim_enc.step()
        #self.net.optim_reg_mu.step()
        #self.net.optim_reg_sigma_ae.step()
        self.net.optim_dec.step()

        self.result.add(name, odict(
            loss_total=loss.item(),
            loss_recon=loss_recon.item(),
            #loss_kl=loss_kl.item(),
            #loss_var=loss_var.item(),
            acc=acc.item(),
            #sigma=self.net.reg.sigma.mean().item(),
            # cosim=cos_sim.item(),
            # var=self.net.reg.var,
            noise=self.net.enc.noise_radius,
        ))

    def _eval_autoencoder(self, batch, name='AE_eval'):
        #name += ('/' + decode_mode)
        n_vars = 10
        assert n_vars > 0
        codes_r = list()

        self.net.set_modules_train_mode(False)

        with torch.no_grad():
            # Build graph
            embed = self.net.embed_w(batch.src)
            #code = self.net.enc.with_noise(embed, batch.len)
            code_ = self.net.enc(embed, batch.len)
            #code = self.net.reg.without_var(enc_h)
            for _ in range(n_vars):
                #code_var = self.net.reg.with_var(code)
                noise, _, _ = self.net.rev(code_)
                code_r = self.net.gen(noise)
                # if self.noise > 0:
                #     code_var = self._add_noise_to(code)
                # else:
                #     code_var = code
                codes_r.append(code_r)

            # noise, _, _ = self.net.rev(code)
            # code_gen = self.net.gen(noise)

            #code_var = self.net.reg.with_var(code)
            #cos_sim = F.cosine_similarity(code, code_var, dim=1).mean()
            assert len(codes_r) > 0
            decoded = self.net.dec(code_, max_len=batch.max_len)

        # Compute word prediction loss and accuracy
        bsz = self.cfg.batch_size
        maxlen = self.cfg.max_len + 1
        #tar = batch.src[:bsz].veiw(bsz, )
        target = batch.tar[:bsz*maxlen]
        #target = batch.src[:bsz].view(-1)
        loss_recon, acc = self._recon_loss_and_acc_for_rnn(
            decoded.prob[:bsz], target, len(self.net.vocab_w))
        #loss_var = 1 / torch.mean(self.net.reg.var)
        #loss_kl = self._compute_kl_div_loss(self.net.reg.mu, self.net.reg.sigma)

        embed = ResultWriter.Embedding(
            embed=code_.data,
            text=decoded.get_text_batch(),
            tag='code_embed')

        # embed_gen = ResultWriter.Embedding(
        #     embed=code_gen.data,
        #     text=decoded.get_text_batch(),
        #     tag='code_embed')

        embeds_r = odict()
        for i in range(n_vars):
            embed_r = ResultWriter.Embedding(
                embed=codes_r[i].data,
                text=decoded.get_text_batch(),
                tag='code_embed')
            embeds_r.update({('embed_noise_%d' % i): embed_r})

        result_dict = odict(
            loss_recon=loss_recon.item(),
            #loss_var=loss_var.item(),
            #loss_kl=loss_kl.item(),
            acc=acc.item(),
            embed_real=embed,
            #embed_gen=embed_gen,
            #embed_recon=embed_r,
            # cosim=cos_sim.item(),
            noise=self.net.enc.noise_radius,
            text=decoded.get_text_with_pair(batch.src),
        )
        result_dict.update(embeds_r)
        self.result.add(name, result_dict)

    def _recon_loss_and_acc_for_rnn(self, output, target, vocab_size):
        output = output.view(-1, vocab_size)  # flatten output
        output, target = mask_output_target(output, target, vocab_size)
        loss = self.net.dec.criterion_nll(output, target)
        _, max_ids = torch.max(output, 1)
        acc = torch.mean(max_ids.eq(target).float())

        return loss, acc

    def _recon_loss_and_acc_for_cnn(self, output, target, vocab_size):
        output = output.view(-1, vocab_size)  # flatten output
        loss = self.net.dec.criterion_nll(output, target)
        _, max_ids = torch.max(output, 1)
        acc = torch.mean(max_ids.eq(target).float())

        return loss, acc

    # def _compute_kl_div_loss(self, mu, sigma):
    #     mu_sq = mu.pow(2)
    #     var = sigma.pow(2)
    #
    #     return - 0.5 * torch.sum(1 + torch.log(var) - mu_sq - var)

    # def _compute_kl_div_loss(self, mu, logvar):
    #     return 0.5 * torch.mean(mu.pow(2) + logvar.exp() - logvar - 1)

    def _compute_kl_div_loss(self, mu, logvar):
        #return 0.5 * torch.sum(mu**2 + sigma**2 - torch.log(sigma**2) - 1)
        return 0.5 * torch.sum(mu**2 + logvar.exp() - logvar - 1, 1)


    def _add_noise_to(self, code):
        # gaussian noise
        noise = torch.normal(mean=torch.zeros(code.size()),
                             std=self.noise)
        noise = to_gpu(self.cfg.cuda, Variable(noise))
        return code + noise

    def _train_regularizer(self, batch, name="Reg_train"):
        self.net.set_modules_train_mode(True)

        # Build graph
        embed = self.net.embed_w(batch.src)
        code_real = self.net.enc(embed, batch.len)
        #code_real = self.net.reg.without_var(enc_h)
        #code_real_var = self.net.reg.with_var(code_real)
        if self.noise > 0:
            code_real_var = self._add_noise_to(code_real)
        else:
            code_real_var = code_real

        # NOTE
        #enc_h.register_hook(self.enc_h_hook.scale_grad_norm)
        #self.net.disc.clamp_weights()
        #disc_real = self.net.disc(code_real_var)
        #disc_real.backward(self.neg_one)

        #self.net.embed_w.clip_grad_norm_()
        #self.net.enc.clip_grad_norm_()
        #self.net.reg.clip_grad_norm_()
        #self.net.optim_embed_w.step()
        #self.net.optim_enc.step()
        #self.net.optim_reg_sigma_gen.step()

        noise = self.net.rev(code_real_var)
        code_rev = self.net.gen(noise.detach())
        rev_dist = F.pairwise_distance(code_rev, code_real.detach(),
                                       p=2).mean() # NOTE code_real_var?
        rev_dist.backward()
        self.net.optim_gen.step()

        self.net.set_modules_train_mode(True)

        with torch.no_grad():
            embed = self.net.embed_w(batch.src)
            code_real = self.net.enc.with_noise(embed, batch.len)
            noise = self.net.rev(code_real)
            code_rev = self.net.gen(noise)

        decoded = self.net.dec2(code_rev, batch=batch)
        gen_fake, gen_acc = self._recon_loss_and_acc_for_rnn(
            decoded.prob, batch.tar, len(self.net.vocab_w))

        gen_fake.backward()
        self.net.optim_dec2.step()

        # code_enc_var = self.net.reg.with_directional_var(code_enc, code_diff)
        # rev_dist = F.pairwise_distance(code_enc_var, code_gen, p=2).mean()
        # #code_enc_var.register_hook(self.tansfer_hook.transfer_grad)
        # rev_dist.backward(retain_graph=True)
        self.result.add(name, odict(
            rev_dist=rev_dist.item(),
            gen_fake=gen_fake.item(),
            gen_acc=gen_acc.item(),
            #sigma=self.net.reg.sigma
            text=decoded.get_text_with_pair(batch.src),
            ))

    def _train_generator(self, name="Gen_train"):
        self.net.set_modules_train_mode(True)

        # Build graph
        noise = self.net.gen.get_noise()
        code_fake = self.net.gen(noise)
        # self.net.disc.clamp_weights()
        # disc_fake = self.net.disc(code_fake)
        # disc_fake.backward(self.pos_one)
        # self.net.optim_gen.step()

        noise_recon = self.net.rev(code_fake.detach())
        rev_dist = F.pairwise_distance(noise, noise_recon, p=2)
        rev_dist.backward()
        self.net.optim_rev.step()

        self.result.add(name, odict(
            #loss_gen=disc_fake.item(),
            loss_rev=rev_dist.item(),
        ))

    def _train_code_vae(self, batch, name="Code_VAE_train"):
        self.net.set_modules_train_mode(True)

        with torch.no_grad():
            embed = self.net.embed_w(batch.src)
            code = self.net.enc(embed, batch.len)

        noise, mu, sigma = self.net.rev(code.detach())
        code_r = self.net.gen(noise)

        loss_recon = (code_r - code.detach()).pow(2).sum(1).mean()
        #loss_recon = F.pairwise_distance(code_r, code.detach(), p=2)
        loss_kl = self._compute_kl_div_loss(mu, sigma).mean() #* 0.1

        #beta = 200
        #normalized_beta = beta * self.cfg.z_size / self.cfg.hidden_size_w
        loss = loss_recon + loss_kl # * 0.01
        loss.backward()

        self.net.optim_rev.step()
        self.net.optim_gen.step()

        self.result.add(name, odict(
            loss_total=loss.item(),
            loss_recon=loss_recon.item(),
            loss_kl=loss_kl.item(),
            sigma=self.net.rev.sigma.item(),
        ))


    def _train_dec2(self, batch, name="Dec2_train"):
        self.net.set_modules_train_mode(True)

        with torch.no_grad():
            embed = self.net.embed_w(batch.src)
            code = self.net.enc(embed, batch.len)
            noise, mu, sigma = self.net.rev(code)
            code_r = self.net.gen(noise)

        decoded = self.net.dec2(code_r.detach(), batch=batch)
        gen_fake, gen_acc = self._recon_loss_and_acc_for_rnn(
            decoded.prob, batch.tar, len(self.net.vocab_w))

        gen_fake.backward()
        self.net.optim_dec2.step()

        self.result.add(name, odict(
            dec2_acc=gen_acc.item(),
            text=decoded.get_text_with_pair(batch.src),
        ))


    def _train_regularizer2(self, batch, name="Reg_train"):
        self.net.set_modules_train_mode(True)

        embed = self.net.embed_w(batch.src)
        enc_h = self.net.enc(embed, batch.len)
        code_var = self.net.reg.with_var(enc_h)
        self.net.disc.clamp_weights()
        disc_var = self.net.disc(code_var)

        #code_var.register_hook(self.code_var_hook.scale_grad_norm)
        disc_var.backward(self.pos_one)
        #self.net.embed_w.clip_grad_norm_()
        #self.net.enc.clip_grad_norm_()
        #self.net.reg.clip_grad_norm_()
        self.net.optim_embed_w.step()
        self.net.optim_enc.step()
        self.net.optim_reg_sigma_gen.step()

    def _train_discriminator(self, batch, name="Disc_train"):
        self.net.set_modules_train_mode(True)

        # Code generation
        embed = self.net.embed_w(batch.src)
        code_real = self.net.enc.with_noise(embed, batch.len)
        code_fake = self.net.gen.for_train()
        #self.net.reg.sigma.register_hook(lambda grad: grad*grad.lt(0).float())

        # Grad hook : gradient scaling
        #code_real.register_hook(self.code_hook.scale_grad_norm)
        #code_posvar.register_hook(self.hook.scale_grad_norm)
        #code_negvar.register_hook(self.hook.scale_grad_norm)

        self.net.disc.clamp_weights()  # Weight clamping for WGAN
        disc_real = self.net.disc(code_real.detach())
        #disc_real_neg = self.net.disc(code_negvar.detach())
        #disc_real_neg = self.net.disc(code_neg)
        disc_fake = self.net.disc(code_fake.detach())
        loss_total = disc_real - disc_fake

        #code_var.register_hook(self.hook_pos.stash_abs_grad)
        #code_neg.register_hook(self.hook_pos.pass_smaller_abs_grad)

        # WGAN backward
        disc_real.backward(self.pos_one)
        disc_fake.backward(self.neg_one)
        # loss_total.backward()
        #self.net.optim_reg_ae.step()
        self.net.optim_disc.step()

        # train encoder adversarilly
        # self.net.embed_w.zero_grad()
        # self.net.enc.zero_grad()
        # self.net.reg.zero_grad()
        # disc_real.backward(self.neg_one)
        # self.net.embed_w.clip_grad_norm_()
        # self.net.enc.clip_grad_norm_()
        # self.net.optim_embed_w.step()
        # self.net.optim_enc.step()
        # self.net.optim_reg_mu.step()

        self.result.add(name, odict(
            loss_toal=loss_total.item(),
            loss_real=disc_real.item(),
            loss_fake=disc_fake.item(),
        ))


    def _generate_text2(self, name="Generated"):
        self.net.set_modules_train_mode(True)

        # Build graph
        noise_size = (self.cfg.eval_size, self.cfg.hidden_size_w)
        code_fake = self.net.dec.get_noise(noise_size)
        decoded = self.net.dec.tester(code_fake, max_len=self.cfg.max_len)

        code_embed = ResultWriter.Embedding(
            embed=code_fake.data,
            text=decoded.get_text_batch(),
            tag='code_embed')

        self.result.add(name, odict(
            embed=code_embed,
            txt_word=decoded.get_text(),
        ))

        # Evaluation
        scores = evaluate_sents(self.test_sents, decoded.get_text())
        self.result.add("Evaluation", scores)


    def _generate_text(self, name="Generated"):
        self.net.set_modules_train_mode(True)

        with torch.no_grad():
            # Build graph
            noise_size = (self.cfg.eval_size, self.cfg.hidden_size_w)
            noise = self.net.dec.make_noise_size_of(noise_size)
            code_fake = self.net.gen.for_eval()
            zs = self._get_interpolated_z(100)
            code_interpolated = self.net.gen(zs)

            #decoded0 = self.net.dec.tester(noise, max_len=self.cfg.max_len)
            decoded1 = self.net.dec.tester(code_fake, max_len=self.cfg.max_len)
            decoded2 = self.net.dec2.tester(code_fake, max_len=self.cfg.max_len)
            decoded3 = self.net.dec2.tester(code_interpolated, max_len=self.cfg.max_len)

        # code_embed_vae = ResultWriter.Embedding(
        #     embed=noise.data,
        #     text=decoded0.get_text_batch(),
        #     tag='code_embed')
        code_embed = ResultWriter.Embedding(
            embed=code_fake.data,
            text=decoded2.get_text_batch(),
            tag='code_embed')

        code_embed_interpolated = ResultWriter.Embedding(
            embed=code_interpolated.data,
            text=decoded3.get_text_batch(),
            tag='code_embed')

        # code_embed2 = ResultWriter.Embedding(
        #     embed=code_fake.data,
        #     text=decoded2.get_text_batch(),
        #     tag='code_embed')

        self.result.add(name, odict(
            #embed_fake_vae=code_embed_vae,
            embed_fake=code_embed,
            embed_interpolated=code_embed_interpolated,
            # embed_fake2=code_embed2,
            #txt_word0=decoded0.get_text(),
            txt_word1=decoded1.get_text(),
            txt_word2=decoded2.get_text(),
        ))

        # Evaluation
        #scores = evaluate_sents(self.test_sents, decoded.get_text())
        #self.result.add("Evaluation", scores)

    def _get_interpolated_z(self, num_samples):
        # sample 2 points and compute the distance btwn them
        z_a = np.random.normal(0, 1, (1, self.cfg.z_size))
        z_b = np.random.normal(0, 1, (1, self.cfg.z_size))
        # get intermediate points by interpolation
        offset = (z_b - z_a) / num_samples
        z = np.vstack([z_a + offset * i for i in range(num_samples)])
        return to_gpu(self.cfg.cuda, Variable(torch.FloatTensor(z)))
