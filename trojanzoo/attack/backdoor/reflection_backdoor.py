# -*- coding: utf-8 -*-

from .badnet import BadNet
from trojanzoo.utils.tensor import to_pil_image, byte2float

import torch
import torch.nn as nn

import numpy as np
from PIL import Image

from trojanzoo.utils import Config
env = Config.env


class Reflection_Backdoor(BadNet):
    name: str = 'reflection_backdoor'

    def __init__(self, candidate_num: int = 100, m: int = 20, selection_iter: int = 10, inner_epoch: int = 1, **kwargs):
        super().__init__(**kwargs)
        self.param_list['reflection'] = ['candidate_num', 'selection_iter', 'm', 'inner_epoch']
        self.candidate_num: int = candidate_num
        self.selection_iter: int = selection_iter
        self.m: int = m
        self.inner_epoch: int = inner_epoch

        kernel = torch.tensor([[0., 1., 0.],
                               [1., -4., 1.],
                               [0., 1., 0.]], device='cpu')
        self.conv2d = nn.Conv2d(1, 1, 3, bias=False, padding=1)
        self.conv2d.weight = nn.Parameter(kernel.view_as(self.conv2d.weight))

    def attack(self, epoch: int, save=False, validate_interval: int = 10, **kwargs):
        W = torch.zeros(self.candidate_num)

        loader = self.dataset.get_dataloader(mode='train', batch_size=self.candidate_num, classes=[self.target_class],
                                             shuffle=True, num_workers=0, pin_memory=False)
        candidate_images, _ = next(iter(loader))
        candidate_images = self.conv2d(candidate_images.mean(1, keepdim=True))

        np.random.seed(env['seed'])
        pick_img_ind = np.random.choice(self.candidate_num, self.m, replace=False).tolist()
        adv_images = candidate_images[pick_img_ind]  # (B, C, H, W)

        for current_iter in range(self.selection_iter):
            print(f'Current Iteration : {current_iter}')
            for i in range(len(adv_images)):
                print(f'    adv image idx : {i}')
                self.get_mark(adv_images[i])
                super().attack(self.inner_epoch, validate_interval=0, indent=8, **kwargs)
                _, target_acc, clean_acc = super().validate_func(verbose=False)
                W[pick_img_ind[i]] = target_acc
                self.model.load()
            # update W
            if self.m < self.candidate_num:
                other_img_ind = list(set(range(self.candidate_num)) - set(pick_img_ind))
                W[other_img_ind] = W[pick_img_ind].median()
            # re-pick top m reflection images
            pick_img_ind = W.argsort().tolist()[:self.m]
            adv_images = candidate_images[pick_img_ind]
        # final training, see performance of best reflection trigger
        best_mark_ind = torch.argsort(self.W).tolist()[0]
        self.get_mark(adv_images[best_mark_ind])
        super().attack(epoch, save=save, **kwargs)

    def get_mark(self, conv_ref_img: torch.Tensor):
        '''
        input is a convolved reflection images, already in same
        shape of any input images, this function will legally reshape
        this ref_img and give to self.mark.mark.
        '''
        org_mark_img: Image.Image = to_pil_image(conv_ref_img)
        org_mark_img = org_mark_img.resize((self.mark.width, self.mark.height), Image.ANTIALIAS)
        self.mark.org_mark: torch.Tensor = byte2float(org_mark_img)

        self.mark.org_mask, self.mark.org_alpha_mask = self.mark.org_mask_mark(self.mark.org_mark,
                                                                               self.mark.edge_color, self.mark.mark_alpha)
        self.mark.mark, self.mark.mask, self.mark.alpha_mask = self.mark.mask_mark(
            height_offset=self.mark.height_offset, width_offset=self.mark.width_offset)