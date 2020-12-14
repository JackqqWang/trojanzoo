# -*- coding: utf-8 -*-

from ..defense import Defense
from trojanzoo.optim.pgd import PGD

import torch
from typing import Tuple


class Grad_Train(Defense):

    name: str = 'grad_train'

    def __init__(self, pgd_alpha: float = 2.0 / 255, pgd_epsilon: float = 8.0 / 255, pgd_iteration: int = 7,
                 grad_lambda: float = 10, **kwargs):
        super().__init__(**kwargs)
        self.param_list['grad_train'] = ['grad_lambda']
        self.grad_lambda = grad_lambda

        self.param_list['adv_train'] = ['pgd_alpha', 'pgd_epsilon', 'pgd_iteration']
        self.pgd_alpha = pgd_alpha
        self.pgd_epsilon = pgd_epsilon
        self.pgd_iteration = pgd_iteration
        self.pgd = PGD(alpha=pgd_alpha, epsilon=pgd_epsilon, iteration=pgd_iteration, stop_threshold=None)

    def detect(self, **kwargs):
        self.model._train(loss_fn=self.loss_fn, validate_func=self.validate_func, verbose=True, **kwargs)

    def loss_fn(self, _input, _label, **kwargs):
        loss_list = []
        new_input = _input.repeat(4, 1, 1, 1)
        new_label = _label.repeat(4)
        noise = torch.randn_like(new_input)
        noise = noise / noise.norm(p=float('inf')) * self.pgd_epsilon
        new_input = new_input + noise
        new_input = new_input.clamp(0, 1).detach()
        new_input.requires_grad_()
        loss = self.model.loss(new_input, new_label)
        grad = torch.autograd.grad(loss, new_input, create_graph=True)[0]
        new_loss = loss + self.grad_lambda * grad.flatten(start_dim=1).norm(p=1, dim=1).mean()
        return new_loss

    def validate_func(self, get_data=None, loss_fn=None, **kwargs) -> Tuple[float, float, float]:
        clean_loss, clean_acc, _ = self.model._validate(print_prefix='Validate Clean',
                                                        get_data=None, **kwargs)
        adv_loss, adv_acc, _ = self.model._validate(print_prefix='Validate Adv',
                                                    get_data=self.get_data, **kwargs)
        # todo: Return value
        if self.clean_acc - clean_acc > 20 and self.clean_acc > 40:
            adv_acc = 0.0
        return clean_loss + adv_loss, adv_acc, clean_acc

    def get_data(self, data: Tuple[torch.Tensor, torch.LongTensor], **kwargs) -> Tuple[torch.Tensor, torch.LongTensor]:
        _input, _label = self.model.get_data(data, **kwargs)

        def loss_fn(X: torch.FloatTensor):
            return -self.model.loss(X, _label)
        adv_x, _ = self.pgd.optimize(_input=_input, loss_fn=loss_fn)
        return adv_x, _label

    def save(self, **kwargs):
        self.model.save(folder_path=self.folder_path, suffix='_grad_train', verbose=True, **kwargs)
