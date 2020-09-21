# -*- coding: utf-8 -*-

from ..defense_backdoor import Defense_Backdoor

from trojanzoo.utils import to_list, normalize_mad
from trojanzoo.utils.model import to_categorical, AverageMeter
from trojanzoo.utils.output import prints, ansi, output_iter
from trojanzoo.utils.defense import get_confidence

import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F

import time
import datetime
from tqdm import tqdm
from typing import List

from trojanzoo.utils.config import Config
env = Config.env


mse_criterion = nn.MSELoss()


class Deep_Inspect(Defense_Backdoor):

    name: str = 'deep_inspect'

    def __init__(self, sample_ratio: float = 0.1, noise_dim: int = 100,
                 remask_epoch: int = 30, remask_lr=0.01,
                 gamma_1: float = 0.0, gamma_2: float = 1, **kwargs):
        super().__init__(**kwargs)
        data_shape = [self.dataset.n_channel]
        data_shape.extend(self.dataset.n_dim)
        self.data_shape: List[int] = data_shape

        self.param_list['deep_inspect'] = ['sample_ratio', 'remask_epoch', 'remask_lr', 'gamma_1', 'gamma_2']

        self.sample_ratio: int = sample_ratio
        self.remask_epoch: int = remask_epoch
        self.remask_lr: float = remask_lr
        self.gamma_1: float = gamma_1
        self.gamma_2: float = gamma_2

        self.noise_dim: int = noise_dim

        dataset = self.dataset.get_dataset(mode='train')
        subset, _ = self.dataset.split_set(dataset, percent=sample_ratio)
        self.loader = self.dataset.get_dataloader(mode='train', dataset=subset)

    def detect(self, **kwargs):
        super().detect(**kwargs)
        self.real_mask = self.attack.mark.mask
        loss_list, mark_list = self.get_potential_triggers()
        print('loss: ', loss_list)  # DeepInspect use this)

    def get_potential_triggers(self) -> (torch.Tensor, torch.Tensor):
        mark_list, loss_list = [], []
        # todo: parallel to avoid for loop
        for label in range(self.model.num_classes):
            print('Class: ', output_iter(label, self.model.num_classes))
            loss, mark = self.remask(label)
            loss_list.append(loss)
            mark_list.append(mark)
        loss_list = torch.as_tensor(loss_list)
        return loss_list, mark_list

    def remask(self, label: int) -> (torch.Tensor, torch.Tensor):
        generator = Generator(self.noise_dim, self.dataset.num_classes, self.data_shape)
        for param in generator.parameters():
            param.requires_grad_()
        optimizer = optim.Adam(generator.parameters(), lr=self.remask_lr)
        optimizer.zero_grad()
        # mask = self.attack.mark.mask

        losses = AverageMeter('Loss', ':.4e')
        entropy = AverageMeter('Entropy', ':.4e')
        norm = AverageMeter('Norm', ':.4e')
        acc = AverageMeter('Acc', ':6.2f')
        torch.manual_seed(env['seed'])
        noise = torch.rand(1, self.noise_dim, device=env['device'])
        for _epoch in range(self.remask_epoch):
            losses.reset()
            entropy.reset()
            norm.reset()
            acc.reset()
            epoch_start = time.perf_counter()
            loader = self.loader
            if env['tqdm']:
                loader = tqdm(loader)
            for data in loader:
                _input, _label = self.model.get_data(data)
                batch_size = _label.size(0)
                poison_label = label * torch.ones_like(_label)
                mark = generator(noise, torch.tensor([label], device=poison_label.device, dtype=poison_label.dtype))
                poison_input = (_input + mark).clamp(0, 1)
                _output = self.model(poison_input)

                batch_acc = poison_label.eq(_output.argmax(1)).float().mean()
                batch_entropy = self.model.criterion(_output, poison_label)
                batch_norm = mark.flatten(start_dim=1).norm(p=1, dim=1).mean()
                batch_loss = batch_entropy + self.gamma_2 * batch_norm

                acc.update(batch_acc.item(), batch_size)
                entropy.update(batch_entropy.item(), batch_size)
                norm.update(batch_norm.item(), batch_size)
                losses.update(batch_loss.item(), batch_size)

                batch_loss.backward()
                optimizer.step()
                optimizer.zero_grad()
            epoch_time = str(datetime.timedelta(seconds=int(
                time.perf_counter() - epoch_start)))
            pre_str = '{blue_light}Epoch: {0}{reset}'.format(
                output_iter(_epoch + 1, self.remask_epoch), **ansi).ljust(64 if env['color'] else 35)
            _str = ' '.join([
                f'Loss: {losses.avg:.4f},'.ljust(20),
                f'Acc: {acc.avg:.2f}, '.ljust(20),
                f'Norm: {norm.avg:.4f},'.ljust(20),
                f'Entropy: {entropy.avg:.4f},'.ljust(20),
                f'Time: {epoch_time},'.ljust(20),
            ])
            prints(pre_str, _str, prefix='{upline}{clear_line}'.format(**ansi) if env['tqdm'] else '', indent=4)

        def get_data_fn(data, **kwargs):
            _input, _label = self.model.get_data(data)
            poison_label = torch.ones_like(_label) * label
            poison_input = (_input + mark).clamp(0, 1)
            return poison_input, poison_label
        self.model._validate(print_prefix='Validate Trigger Tgt', get_data=get_data_fn, indent=4)

        detect_mask = mark > 0.1
        sum_temp = detect_mask.int() + self.real_mask.int()
        overlap = (sum_temp == 2).sum().float() / (sum_temp >= 1).sum().float()
        print(f'    Jaccard index: {overlap:.3f}')

        for param in generator.parameters():
            param.requires_grad = False
        return losses.avg, mark

    # def loss(self, _output: torch.Tensor, mark: torch.Tensor, poison_label: torch.LongTensor) -> torch.Tensor:
    #     loss_trigger = self.model.criterion(_output, poison_label)
    #     # loss_gan = self.loss_gan(_output, label)
    #     loss_pert = self.loss_pert(mark)
    #     return loss_trigger + self.gamma_2 * loss_pert  # self.gamma_1 * loss_gan +

    # def loss_gan(self, _output: torch.Tensor, label: int) -> torch.Tensor:
    #     to_categorical = torch.zeros_like(_output)
    #     to_categorical[:, label] = 1.0
    #     return self.mse_criterion(_output, to_categorical)

    # @staticmethod
    # def loss_pert(self, mark: torch.Tensor) -> torch.Tensor:
    #     return mark.norm(p=1)


class Generator(nn.Module):
    def __init__(self, noise_dim: int = 100, num_classes: int = 10, data_shape: List[int] = [3, 32, 32]):
        self.noise_dim: int = noise_dim
        self.num_classes: int = num_classes
        self.data_shape: List[int] = data_shape
        super(Generator, self).__init__()

        self.relu = nn.ReLU(inplace=True)
        self.sigmoid = nn.Sigmoid()
        self.fc2 = nn.Linear(self.num_classes, 1000)
        self.fc = nn.Linear(self.noise_dim + 1000, 64 * self.data_shape[1] * self.data_shape[2])
        self.bn1 = nn.BatchNorm2d(64)
        self.deconv1 = nn.ConvTranspose2d(64, 32, 5, 1, 2)
        self.bn2 = nn.BatchNorm2d(32)
        self.deconv2 = nn.ConvTranspose2d(32, self.data_shape[0], 5, 1, 2)
        if env['num_gpus']:
            self.cuda()

    def forward(self, noise: torch.Tensor, poison_label: torch.LongTensor) -> torch.Tensor:
        _label = to_categorical(poison_label, self.num_classes).float()
        y_ = self.fc2(_label)
        y_ = self.relu(y_)
        x = torch.cat([noise, y_], dim=1)
        x = self.fc(x)
        x = x.view(-1, 64, self.data_shape[1], self.data_shape[2])
        x = self.bn1(x)
        x = self.relu(x)
        x = self.deconv1(x)
        x = self.bn2(x)
        x = self.relu(x)
        x = self.deconv2(x)
        x = self.sigmoid(x)
        return x
