
# -*- coding: utf-8 -*-

from trojanzoo import __file__ as root_file
from trojanzoo.dataset import Dataset
from trojanzoo.environ import env
from trojanzoo.utils import to_tensor, to_numpy, byte2float, gray_img, save_tensor_as_img
from trojanzoo.utils.config import Config
from trojanzoo.utils.output import ansi, prints, Indent_Redirect

import os
import sys
import random
import numpy as np
import torch
import argparse
from PIL import Image
from collections import OrderedDict
from typing import Callable, List, Dict, Tuple, Type, Union

root_dir = os.path.dirname(os.path.abspath(root_file))
redirect = Indent_Redirect(buffer=True, indent=0)


def add_argument(parser: argparse.ArgumentParser):
    group = parser.add_argument_group('{yellow}mark{reset}'.format(**ansi))
    group.add_argument('--edge_color', dest='edge_color',
                       help='edge color in watermark image, defaults to \'auto\'.')
    group.add_argument('--mark_path', dest='mark_path',
                       help='edge color in watermark image, defaults to trojanzoo/data/mark/apple_white.png.')
    group.add_argument('--mark_alpha', dest='mark_alpha', type=float,
                       help='mark transparency, defaults to 0.0.')
    group.add_argument('--mark_height', dest='mark_height', type=int,
                       help='mark height, defaults to 3.')
    group.add_argument('--mark_width', dest='mark_width', type=int,
                       help='mark width, defaults to 3.')
    group.add_argument('--height_offset', dest='height_offset', type=int,
                       help='height offset, defaults to 0')
    group.add_argument('--width_offset', dest='width_offset', type=int,
                       help='width offset, defaults to 0')
    group.add_argument('--random_pos', dest='random_pos', action='store_true',
                       help='Random offset Location for add_mark.')
    group.add_argument('--random_init', dest='random_init', action='store_true',
                       help='random values for mark pixel.')
    group.add_argument('--mark_distributed', dest='mark_distributed', action='store_true',
                       help='Distributed Mark.')
    return group


def create(data_shape=None, dataset_name: str = None, dataset: Dataset = None, **kwargs):
    if data_shape is None:
        assert isinstance(dataset, Dataset)
        data_shape: list = [dataset.n_channel]
        data_shape.extend(dataset.n_dim)
    if dataset_name is None and dataset is not None:
        dataset_name = dataset.name
    result = Config.combine_param(config=Config.config['mark'], dataset_name=dataset_name, **kwargs)
    return Watermark(data_shape=data_shape, **result)


class Watermark:
    name: str = 'mark'

    def __init__(self, data_shape: List[int], edge_color: Union[str, torch.Tensor] = 'auto',
                 mark_path: str = 'trojanzoo/data/mark/square_white.png', mark_alpha: float = 0.0,
                 mark_height: int = None, mark_width: int = None,
                 height_offset: int = 0, width_offset: int = 0,
                 random_pos=False, random_init=False, mark_distributed=False,
                 add_mark_fn=None, **kwargs):
        self.param_list: Dict[str, List[str]] = OrderedDict()
        self.param_list['mark'] = ['mark_path', 'data_shape', 'edge_color',
                                   'mark_alpha', 'mark_height', 'mark_width',
                                   'random_pos', 'random_init']
        assert mark_height > 0 and mark_width > 0
        # --------------------------------------------------- #

        # WaterMark Image Parameters
        self.mark_alpha: float = mark_alpha
        self.data_shape: List[int] = data_shape
        self.mark_path: str = mark_path
        self.mark_height: int = mark_height
        self.mark_width: int = mark_width
        self.random_pos = random_pos
        self.random_init = random_init
        self.mark_distributed = mark_distributed
        self.add_mark_fn: Callable = add_mark_fn
        # --------------------------------------------------- #

        if self.mark_distributed:
            self.mark = torch.rand(data_shape, dtype=torch.float, device=env['device'])
            mask = torch.zeros(data_shape[-2:], dtype=torch.bool, device=env['device']).flatten()
            np.random.seed(env['seed'])
            idx = np.random.choice(len(mask), self.mark_height * self.mark_width, replace=False).tolist()
            mask[idx] = 1.0
            mask = mask.view(data_shape[-2:])
            self.mask = mask
            self.alpha_mask = self.mask * (1 - mark_alpha)
            self.edge_color = None
        else:
            org_mark_img: Image.Image = self.load_img(img_path=mark_path,
                                                      height=mark_height, width=mark_width, channel=data_shape[0])
            self.org_mark: torch.Tensor = byte2float(org_mark_img)
            self.edge_color: torch.Tensor = self.get_edge_color(
                self.org_mark, data_shape, edge_color)
            self.org_mask, self.org_alpha_mask = self.org_mask_mark(self.org_mark, self.edge_color, self.mark_alpha)
            if random_init:
                self.org_mark = self.random_init_mark(self.org_mark, self.org_mask)
            if not random_pos:
                self.param_list['mark'].extend(['height_offset', 'width_offset'])
                self.height_offset: int = height_offset
                self.width_offset: int = width_offset
                self.mark, self.mask, self.alpha_mask = self.mask_mark()

    # add mark to the Image with mask.

    def add_mark(self, _input: torch.Tensor, random_pos=None, alpha: float = None, **kwargs) -> torch.Tensor:
        if self.add_mark_fn is not None:
            return self.add_mark_fn(_input, random_pos=random_pos, alpha=alpha, **kwargs)
        if random_pos is None:
            random_pos = self.random_pos
        if random_pos:
            # batch_size = _input.size(0)
            # height_offset = torch.randint(high=self.data_shape[-2] - self.mark_height, size=[batch_size])
            # width_offset = torch.randint(high=self.data_shape[-1] - self.mark_width, size=[batch_size])
            height_offset = random.randint(0, self.data_shape[-2] - self.mark_height)
            width_offset = random.randint(0, self.data_shape[-1] - self.mark_width)
            mark, mask, alpha_mask = self.mask_mark(height_offset=height_offset, width_offset=width_offset)
        else:
            mark, mask, alpha_mask = self.mark, self.mask, self.alpha_mask
            if alpha is not None:
                alpha_mask = torch.ones_like(self.alpha_mask) * (1 - alpha)
        _mask = mask * alpha_mask
        mark, _mask = mark.to(_input.device), _mask.to(_input.device)
        return _input + _mask * (mark - _input)

    @staticmethod
    def get_edge_color(mark: torch.Tensor, data_shape: List[int],
                       edge_color: Union[str, torch.Tensor] = 'auto') -> torch.Tensor:

        assert data_shape[0] == mark.shape[0]
        t: torch.Tensor = torch.zeros(data_shape[0], dtype=torch.float)
        if isinstance(edge_color, str):
            if edge_color == 'black':
                pass
            elif edge_color == 'white':
                t += 1
            elif edge_color == 'auto':
                mark = mark.transpose(0, -1)
                if mark.flatten(start_dim=1).std(dim=1).max() < 1e-3:
                    t = -torch.ones_like(mark[0, 0])
                else:
                    _list = [mark[0, :, :], mark[-1, :, :],
                             mark[:, 0, :], mark[:, -1, :]]
                    _list = torch.cat(_list)
                    t = _list.mode(dim=0)[0]
            else:
                raise ValueError(edge_color)
        else:
            t = torch.as_tensor(edge_color)
            assert len(t.shape) == 1
            assert t.shape[0] == data_shape[0]
        return t

    @staticmethod
    def org_mask_mark(org_mark: torch.Tensor, edge_color: torch.Tensor, mark_alpha: float) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        height, width = org_mark.shape[-2:]
        mark = torch.zeros_like(org_mark, dtype=torch.float)
        mask = torch.zeros([height, width], dtype=torch.bool)
        for i in range(height):
            for j in range(width):
                if not org_mark[:, i, j].equal(edge_color):
                    mark[:, i, j] = org_mark[:, i, j]
                    mask[i, j] = 1
        alpha_mask = mask * (1 - mark_alpha)
        return mask, alpha_mask

    def mask_mark(self, org_mark: torch.Tensor = None, org_mask: torch.Tensor = None, org_alpha_mask: torch.Tensor = None,
                  height_offset: int = None, width_offset: int = None) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        if org_mark is None:
            org_mark = self.org_mark
        if org_mask is None:
            org_mask = self.org_mask
        if org_alpha_mask is None:
            org_alpha_mask = self.org_alpha_mask
        if height_offset is None:
            height_offset = self.height_offset
        if width_offset is None:
            width_offset = self.width_offset
        mark = -torch.ones(self.data_shape, dtype=torch.float)
        mask = torch.zeros(self.data_shape[-2:], dtype=torch.bool)
        alpha_mask = torch.zeros_like(mask, dtype=torch.float)

        start_h = height_offset
        start_w = width_offset
        end_h = height_offset + self.mark_height
        end_w = width_offset + self.mark_width

        mark[:, start_h:end_h, start_w:end_w] = org_mark
        mask[start_h:end_h, start_w:end_w] = org_mask
        alpha_mask[start_h:end_h, start_w:end_w] = org_alpha_mask
        if env['num_gpus']:
            mark = mark.to(env['device'])
            mask = mask.to(env['device'])
            alpha_mask = alpha_mask.to(env['device'])
        return mark, mask, alpha_mask

    """
    # each image in the batch has a unique random location.
    def mask_mark_batch(self, height_offset: torch.Tensor, width_offset: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        assert len(height_offset) == len(width_offset)
        shape = [len(height_offset)].extend(self.data_shape)
        mark = -torch.ones(shape, dtype=int)
        shape[1] = 1
        mask = torch.zeros(shape, dtype=torch.float)
        alpha_mask = torch.zeros_like(mask)

        start_h = height_offset
        start_w = width_offset
        end_h = height_offset + self.mark_height
        end_w = width_offset + self.mark_width

        mark[:, start_h:end_h, start_w:end_w] = self.org_mark
        mask[start_h:end_h, start_w:end_w] = self.org_mask
        alpha_mask[start_h:end_h, start_w:end_w] = self.org_alpha_mask

        mark = to_tensor(mark)
        mask = to_tensor(mask)
        alpha_mask = to_tensor(alpha_mask)
        return mark, mask, alpha_mask
    """

    # Give the mark init values for non transparent pixels.
    @staticmethod
    def random_init_mark(mark, mask):
        init_mark = torch.rand_like(mark)
        ones = -torch.ones_like(mark)
        init_mark = torch.where(mask, init_mark, ones)
        return init_mark

    # ------------------------------ I/O --------------------------- #

    @staticmethod
    def load_img(img_path: str, height: int, width: int, channel: int = 3) -> Image.Image:
        if img_path[:9] == 'trojanzoo':
            img_path = root_dir + img_path[9:]
        mark: Image.Image = Image.open(img_path)
        mark = mark.resize((width, height), Image.ANTIALIAS)

        if channel == 1:
            mark = gray_img(mark, num_output_channels=1)
        elif channel == 3 and mark.mode in ['1', 'L']:
            mark = gray_img(mark, num_output_channels=3)
        return mark

    def save_img(self, img_path: str):
        img = self.org_mark * self.org_mask if self.random_pos else self.mark * self.mask
        save_tensor_as_img(img_path, img)

    def load_npz(self, npz_path: str):
        if npz_path[:9] == 'trojanzoo':
            npz_path = root_dir + npz_path[9:]
        _dict = np.load(npz_path)
        if not self.mark_distributed:
            self.org_mark = torch.as_tensor(_dict['org_mark'])
            self.org_mask = torch.as_tensor(_dict['org_mask'])
            self.org_alpha_mask = torch.as_tensor(_dict['org_alpha_mask'])
        if not self.random_pos:
            self.mark = to_tensor(_dict['mark'])
            self.mask = to_tensor(_dict['mask'])
            self.alpha_mask = to_tensor(_dict['alpha_mask'])

    def save_npz(self, npz_path: str):
        _dict = {}
        if not self.mark_distributed:
            _dict.update({'org_mark': to_numpy(self.org_mark),
                          'org_mask': to_numpy(self.org_mask),
                          'org_alpha_mask': to_numpy(self.org_alpha_mask)})
        if not self.random_pos:
            _dict.update({
                'mark': to_numpy(self.mark),
                'mask': to_numpy(self.mask),
                'alpha_mask': to_numpy(self.alpha_mask)
            })
        np.savez(npz_path, **_dict)

    # ------------------------------Verbose Information--------------------------- #
    def summary(self, indent: int = 0):
        prints('{blue_light}{0:<20s}{reset} Parameters: '.format(self.name, **ansi), indent=indent)
        for key, value in self.param_list.items():
            prints('{green}{0:<20s}{reset}'.format(key, **ansi), indent=indent + 10)
            prints({v: getattr(self, v) for v in value}, indent=indent + 10)
            prints('-' * 20, indent=indent + 10)

    def __str__(self) -> str:
        sys.stdout = redirect
        self.summary()
        _str = redirect.buffer
        redirect.reset()
        return _str
