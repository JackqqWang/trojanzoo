# -*- coding: utf-8 -*-

from trojanzoo.utils import add_noise, empty_cache, repeat_to_batch
from trojanzoo.utils.output import prints, ansi, output_iter
from trojanzoo.utils.model import split_name as func
from trojanzoo.utils.model import AverageMeter, CrossEntropy
from trojanzoo.utils.sgm import register_hook
from trojanzoo.dataset.dataset import Dataset

import types
from typing import Union, Callable

import os
import time
import datetime
from collections import OrderedDict
from collections.abc import Iterable
from tqdm import tqdm
import itertools

import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np

from trojanzoo.utils import Config
env = Config.env


class _Model(nn.Module):
    def __init__(self, num_classes: int = None, conv_depth=0, conv_dim=0, fc_depth=0, fc_dim=0, **kwargs):
        super().__init__()

        self.conv_depth = conv_depth
        self.conv_dim = conv_dim
        self.fc_depth = fc_depth
        self.fc_dim = fc_dim
        self.num_classes = num_classes

        self.features = self.define_features()   # feature extractor
        self.pool = nn.AdaptiveAvgPool2d((1, 1))  # average pooling
        self.flatten = nn.Flatten(start_dim=1)
        self.classifier = self.define_classifier()  # classifier

    # forward method
    # input: (batch_size, channels, height, width)
    # output: (batch_size, logits)
    def forward(self, x: torch.Tensor, **kwargs) -> torch.Tensor:
        # if x.shape is (channels, height, width)
        # (channels, height, width) ==> (batch_size: 1, channels, height, width)
        x = self.get_final_fm(x)
        x = self.get_logits_from_fm(x)
        return x

    # input: (batch_size, channels, height, width)
    # output: (batch_size, [feature_map])
    def get_fm(self, x):
        return self.features(x)

    # get feature map
    # input: (batch_size, channels, height, width)
    # output: (batch_size, [feature_map])
    def get_final_fm(self, x):
        x = self.get_fm(x)
        x = self.pool(x)
        x = self.flatten(x)
        return x

    # get logits from feature map
    # input: (batch_size, [feature_map])
    # output: (batch_size, logits)
    def get_logits_from_fm(self, x):
        return self.classifier(x)

    def define_features(self, conv_depth: int = None, conv_dim: int = None):
        return nn.Identity()

    def define_classifier(self, num_classes: int = None, conv_dim: int = None, fc_depth: int = None, fc_dim: int = None):
        if fc_depth is None:
            fc_depth = self.fc_depth
        if self.fc_depth <= 0:
            return nn.Identity()
        if conv_dim is None:
            conv_dim = self.conv_dim
        if fc_dim is None:
            fc_dim = self.fc_dim
        if num_classes is None:
            num_classes = self.num_classes

        seq = []
        if self.fc_depth == 1:
            seq.append(('fc', nn.Linear(self.conv_dim, self.num_classes)))
        else:
            seq.append(('fc1', nn.Linear(self.conv_dim, self.fc_dim)))
            seq.append(('relu1', nn.ReLU()))
            seq.append(('dropout1', nn.Dropout()))
            for i in range(self.fc_depth - 2):
                seq.append(
                    ('fc' + str(i + 2), nn.Linear(self.fc_dim, self.fc_dim)))
                seq.append(('relu' + str(i + 2), nn.ReLU()))
                seq.append(('dropout' + str(i + 2), nn.Dropout()))
            seq.append(('fc' + str(self.fc_depth),
                        nn.Linear(self.fc_dim, self.num_classes)))
        return nn.Sequential(OrderedDict(seq))


class Model:

    def __init__(self, name='model', model_class=_Model, dataset: Dataset = None,
                 num_classes: int = None, loss_weights: torch.FloatTensor = None,
                 official=False, pretrain=False, sgm=False, sgm_gamma: float = 1.0,
                 suffix='', folder_path: str = None, **kwargs):
        self.name: str = name
        self.dataset = dataset
        self.suffix = suffix

        # ------------Auto-------------- #
        if dataset:
            data_dir: str = env['data_dir']
            if isinstance(dataset, str):
                raise TypeError(dataset)
            if folder_path is None:
                folder_path = data_dir + dataset.data_type + '/' + dataset.name + '/model/'
            if num_classes is None:
                num_classes = dataset.num_classes
            if loss_weights is None:
                loss_weights = dataset.loss_weights
        self.num_classes = num_classes  # number of classes
        self.loss_weights = loss_weights

        self.folder_path = folder_path

        # ------------------------------ #
        self.criterion = self.define_criterion(loss_weights=loss_weights)
        self.softmax = nn.Softmax(dim=1)

        # -----------Temp--------------- #
        # the location when loading pretrained weights using torch.load
        self._model: model_class = model_class(num_classes=num_classes, **kwargs)
        self.activate_params([])
        self.model = self.get_parallel()
        # load pretrained weights
        if official:
            self.load('official')
        if pretrain:
            self.load()
        if env['num_gpus']:
            self.cuda()
        self.sgm: bool = sgm
        self.sgm_gamma: float = sgm_gamma
        if sgm:
            register_hook(self, sgm_gamma)
        self.eval()

    # ----------------- Forward Operations ----------------------#

    def get_logits(self, _input: torch.Tensor, randomized_smooth=False, sigma=0.1, n=100, **kwargs):
        if randomized_smooth:
            _list = []
            for _ in range(n):
                _input_noise = add_noise(_input, std=sigma)
                _list.append(self.model(_input_noise, **kwargs))
            return torch.stack(_list).mean(dim=0)
            # _input_noise = add_noise(repeat_to_batch(_input, batch_size=n), std=sigma).flatten(end_dim=1)
            # return self.model(_input_noise, **kwargs).view(n, len(_input), self.num_classes).mean(dim=0)
        else:
            return self.model(_input, **kwargs)

    def get_prob(self, _input, **kwargs) -> torch.Tensor:
        return self.softmax(self.get_logits(_input, **kwargs))

    def get_target_prob(self, _input, target, **kwargs):
        return self.get_prob(_input, **kwargs).gather(dim=1, index=target.unsqueeze(1)).flatten()

    def get_class(self, _input, **kwargs):
        return self.get_logits(_input, **kwargs).argmax(dim=-1)

    def loss(self, _input, _label, **kwargs):
        _output = self(_input, **kwargs)
        return self.criterion(_output, _label)

    # -------------------------------------------------------- #

    # Define the optimizer
    # and transfer to that tuning mode.
    # train_opt: 'full' or 'partial' (default: 'partial')
    # lr: (default: [full:2e-3, partial:2e-4])
    # optim_type: to be implemented
    #
    # return: optimizer

    def define_optimizer(self, lr: float = 0.1,
                         parameters: Union[str, Iterable] = 'full', optim_type: Union[str, type] = None,
                         lr_scheduler=True, step_size=30, **kwargs):

        if isinstance(parameters, str):
            parameters = self.get_params(name=parameters)
        if not isinstance(parameters, Iterable):
            raise TypeError(type(parameters))
        if optim_type is None:
            optim_type = optim.SGD
        elif isinstance(optim_type, str):
            optim_type = getattr(optim, optim_type)
        assert isinstance(optim_type, type)

        if kwargs == {}:
            if optim_type == optim.SGD:
                kwargs = {'momentum': 0.9,
                          'weight_decay': 2e-4, 'nesterov': True}
        optimizer = optim_type(parameters, lr, **kwargs)
        _lr_scheduler = None
        if lr_scheduler:
            _lr_scheduler = optim.lr_scheduler.StepLR(
                optimizer, step_size=step_size, gamma=0.1)
            # optimizer = optim.lr_scheduler.MultiStepLR(
            #     optimizer, milestones=[150, 250], gamma=0.1)
        return optimizer, _lr_scheduler

    # define loss function
    # Cross Entropy
    def define_criterion(self, loss_weights: torch.FloatTensor = None):
        entropy_fn = nn.CrossEntropyLoss(weight=loss_weights)

        def loss_fn(_output: torch.Tensor, _label: torch.LongTensor):
            if self.loss_weights is not None:
                _output = _output.to(device=self.loss_weights.device, dtype=self.loss_weights.dtype)
            return entropy_fn(_output, _label)
        return loss_fn

    # -----------------------------Load & Save Model------------------------------------------- #

    # file_path: (default: '') if '', use the default path. Else if the path doesn't exist, quit.
    # full: (default: False) whether save feature extractor.
    # output: (default: False) whether output help information.
    def load(self, file_path: str = None, folder_path: str = None, suffix: str = None,
             features=True, map_location='default', verbose=False, **kwargs):
        if map_location:
            if map_location == 'default':
                map_location = env['device']
        if file_path is None:
            if folder_path is None:
                folder_path = self.folder_path
            if suffix is None:
                suffix = self.suffix
            file_path = folder_path + self.name + suffix + '.pth'
        elif file_path == 'official':
            return self.load_official_weights()
        if os.path.exists(file_path):
            try:
                if features:
                    self._model.load_state_dict(
                        torch.load(file_path, map_location=map_location))
                else:
                    self._model.classifier.load_state_dict(
                        torch.load(file_path, map_location=map_location))
            except Exception as e:
                print(f'Model file path: {file_path}')
                raise e
        else:
            raise FileNotFoundError(f'Model file not exist: {file_path}')
        if verbose:
            print(f'Model {self.name} loaded from: {file_path}')

    # file_path: (default: '') if '', use the default path.
    # full: (default: False) whether save feature extractor.
    def save(self, file_path: str = None, folder_path: str = None, suffix: str = None, features=True, verbose=False, **kwargs):
        if file_path is None:
            if folder_path is None:
                folder_path = self.folder_path
            if suffix is None:
                suffix = self.suffix
            file_path = folder_path + self.name + suffix + '.pth'
        else:
            folder_path = os.path.dirname(file_path)

        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
        _dict = self._model.state_dict() if features else self._model.classifier.state_dict()
        torch.save(_dict, file_path)
        if verbose:
            print(f'Model {self.name} saved at: {file_path}')

    # define in concrete model class.
    def load_official_weights(self, verbose=True):
        raise NotImplementedError(f'{self.name} has no official weights.')

    # -----------------------------------Train and Validate------------------------------------ #
    def _train(self, epoch: int, optimizer: optim.Optimizer, lr_scheduler: optim.lr_scheduler._LRScheduler = None,
               validate_interval=10, save=False, amp: bool = False, suffix: str = None, verbose=True, indent=0,
               loader_train: torch.utils.data.DataLoader = None, loader_valid: torch.utils.data.DataLoader = None,
               get_data: Callable = None, loss_fn: Callable[[torch.Tensor, torch.LongTensor], torch.Tensor] = None,
               validate_func: Callable = None, epoch_func: Callable = None, save_fn=None, **kwargs):

        if loader_train is None:
            loader_train = self.dataset.loader['train']
        if get_data is None:
            get_data = self.get_data
        if loss_fn is None:
            loss_fn = self.loss
        if validate_func is None:
            validate_func = self._validate
        if save_fn is None:
            save_fn = self.save

        if amp:
            scaler = torch.cuda.amp.GradScaler()

        _, best_acc, _ = validate_func(loader=loader_valid, get_data=get_data, loss_fn=loss_fn,
                                       verbose=verbose, indent=indent, **kwargs)
        self.train()

        # batch_time = AverageMeter('Time', ':6.3f')
        # data_time = AverageMeter('Data', ':6.3f')
        losses = AverageMeter('Loss', ':.4e')
        top1 = AverageMeter('Acc@1', ':6.2f')
        top5 = AverageMeter('Acc@5', ':6.2f')
        # progress = ProgressMeter(
        #     len(trainloader),
        #     [batch_time, data_time, losses, top1, top5],
        #     prefix=f'Epoch: [{epoch}]')

        self.activate_params(optimizer.param_groups[0]['params'])
        optimizer.zero_grad()
        # start = time.perf_counter()
        # end = start
        for _epoch in range(epoch):
            losses.reset()
            top1.reset()
            top5.reset()
            epoch_start = time.perf_counter()
            if verbose:
                loader_train = tqdm(loader_train)
            if epoch_func is not None:
                epoch_func()
            for data in loader_train:
                # data_time.update(time.perf_counter() - end)
                _input, _label = get_data(data, mode='train')
                if amp:
                    loss = loss_fn(_input, _label, amp=True)
                    scaler.scale(loss).backward()
                    scaler.step(optimizer)
                    scaler.update()
                else:
                    loss = loss_fn(_input, _label)
                    loss.backward()
                    optimizer.step()
                optimizer.zero_grad()
                with torch.no_grad():
                    _output = self.get_logits(_input)
                acc1, acc5 = self.accuracy(_output, _label, topk=(1, 5))
                batch_size = int(_label.size(0))
                losses.update(loss.item(), batch_size)
                top1.update(acc1, batch_size)
                top5.update(acc5, batch_size)
                empty_cache()
            epoch_time = str(datetime.timedelta(seconds=int(
                time.perf_counter() - epoch_start)))
            if verbose:
                pre_str = '{blue_light}Epoch: {0}{reset}'.format(
                    output_iter(_epoch + 1, epoch), **ansi).ljust(64)
                _str = ' '.join([
                    f'Loss: {losses.avg:.4f},'.ljust(20),
                    f'Top1 Acc: {top1.avg:.3f}, '.ljust(20),
                    f'Top5 Acc: {top5.avg:.3f},'.ljust(20),
                    f'Time: {epoch_time},'.ljust(20),
                ])
                prints(pre_str, _str, prefix='{upline}{clear_line}'.format(**ansi), indent=indent)
            if lr_scheduler:
                lr_scheduler.step()

            if validate_interval != 0:
                if (_epoch + 1) % validate_interval == 0 or _epoch == epoch - 1:
                    _, cur_acc, _ = validate_func(loader=loader_valid, get_data=get_data, loss_fn=loss_fn,
                                                  verbose=verbose, indent=indent, **kwargs)
                    self.train()
                    if cur_acc >= best_acc:
                        prints('best result update!', indent=indent)
                        best_acc = cur_acc
                        if save:
                            save_fn(suffix=suffix, verbose=verbose)
                    if verbose:
                        print('-' * 50)
        self.zero_grad()
        self.eval()
        self.activate_params([])

    def _validate(self, full=True, print_prefix='Validate', indent=0, verbose=True,
                  loader: torch.utils.data.DataLoader = None,
                  get_data: Callable = None, loss_fn: Callable[[torch.Tensor, torch.LongTensor], float] = None, **kwargs) -> (float, float, float):
        self.eval()
        if loader is None:
            loader = self.dataset.loader['valid'] if full else self.dataset.loader['valid2']
        if get_data is None:
            get_data = self.get_data
        if loss_fn is None:
            loss_fn = self.loss

        # batch_time = AverageMeter('Time', ':6.3f')
        losses = AverageMeter('Loss', ':.4e')
        top1 = AverageMeter('Acc@1', ':6.2f')
        top5 = AverageMeter('Acc@5', ':6.2f')
        # progress = ProgressMeter(
        #     len(self.dataset.loader['valid']),
        #     [batch_time, losses, top1, top5],
        #     prefix='Test: ')

        # start = time.perf_counter()
        # end = start
        epoch_start = time.perf_counter()
        if verbose:
            loader = tqdm(loader)
        with torch.no_grad():
            for data in loader:
                _input, _label = get_data(data, mode='valid', **kwargs)
                loss = loss_fn(_input, _label)
                _output = self.get_logits(_input)

                # measure accuracy and record loss
                acc1, acc5 = self.accuracy(_output, _label, topk=(1, 5))
                losses.update(loss.item(), _label.size(0))

                batch_size = int(_label.size(0))
                top1.update(acc1, batch_size)
                top5.update(acc5, batch_size)

                # empty_cache()

                # measure elapsed time
                # batch_time.update(time.perf_counter() - end)
                # end = time.perf_counter()

                # if i % 10 == 0:
                #     progress.display(i)
        epoch_time = str(datetime.timedelta(seconds=int(
            time.perf_counter() - epoch_start)))
        if verbose:
            pre_str = '{yellow}{0}:{reset}'.format(print_prefix, **ansi).ljust(35)
            _str = ' '.join([
                f'Loss: {losses.avg:.4f},'.ljust(20),
                f'Top1 Acc: {top1.avg:.3f}, '.ljust(20),
                f'Top5 Acc: {top5.avg:.3f},'.ljust(20),
                f'Time: {epoch_time},'.ljust(20),
            ])
            prints(pre_str, _str, prefix='{upline}{clear_line}'.format(**ansi), indent=indent)
        return losses.avg, top1.avg, top5.avg

    # -------------------------------------------Utility--------------------------------------- #

    def get_data(self, data, **kwargs):
        if self.dataset:
            return self.dataset.get_data(data, **kwargs)
        else:
            return data

    def accuracy(self, _output: torch.FloatTensor, _label: torch.LongTensor, topk=(1, 5)):
        """Computes the precision@k for the specified values of k"""
        with torch.no_grad():
            maxk = min(max(topk), self.num_classes)
            batch_size = _label.size(0)

            _, pred = _output.topk(maxk, 1, True, True)
            pred = pred.t()
            correct = pred.eq(_label.view(1, -1).expand_as(pred))

            res = []
            for k in topk:
                if k > self.num_classes:
                    res.append(100.0)
                else:
                    correct_k = correct[:k].view(-1).float().sum(0,
                                                                 keepdim=True)
                    res.append(float(correct_k.mul_(100.0 / batch_size)))
            return res

    def get_params(self, name: str) -> Iterable:
        if name == 'full':
            params = self._model.parameters()
        elif name == 'features':
            params = self._model.features.parameters()
        elif name in ['classifier', 'partial']:
            params = self._model.classifier.parameters()
        else:
            raise NotImplementedError(name)
        return params

    def activate_params(self, active_param: list):
        for param in self._model.parameters():
            param.requires_grad = False
        for param in active_param:
            param.requires_grad_()

    def get_parallel(self):
        if env['num_gpus'] > 1:
            if self.dataset:
                if self.dataset.data_type != 'image':
                    return self._model
            elif self.name[0] == 'g' and self.name[2] == 'n':
                return self._model
            return nn.DataParallel(self._model).cuda()
        else:
            return self._model

    @staticmethod
    def output_layer_information(layer, depth=0, indent=0, verbose=False, tree_length=None):
        if tree_length is None:
            tree_length = 10 * (depth + 1)
        if depth > 0:
            for name, module in layer.named_children():
                _str = '{blue_light}{0}{reset}'.format(name, **ansi)
                if verbose:
                    _str = _str.ljust(
                        tree_length - indent + len(ansi['blue_light']) + len(ansi['reset']))
                    item = str(module).split('\n')[0]
                    if item[-1] == '(':
                        item = item[:-1]
                    _str += item
                prints(_str, indent=indent)
                Model.output_layer_information(
                    module, depth=depth - 1, indent=indent + 10, verbose=verbose, tree_length=tree_length)

    def summary(self, depth=0, indent=0, **kwargs):
        _str = '{blue_light}{0}{reset}'.format(self.name, **ansi)
        prints(_str, indent=indent)
        self.output_layer_information(self._model, depth=depth, indent=indent + 10, **kwargs)

    @staticmethod
    def split_name(name, layer=None, default_layer=0, output=False):
        return func(name, layer=layer, default_layer=default_layer, output=output)

    # -----------------------------------------Reload------------------------------------------ #

    def __call__(self, *args, amp=False, **kwargs):
        if amp:
            with torch.cuda.amp.autocast():
                return self.get_logits(*args, **kwargs)
        return self.get_logits(*args, **kwargs)

    # def __str__(self):
    #     return self.summary()

    # def __repr__(self):
    #     return self.summary()

    def train(self, mode=True):
        self._model.train(mode=mode)
        self.model.train(mode=mode)
        return self

    def eval(self):
        self._model.eval()
        self.model.eval()
        return self

    def cpu(self):
        self._model.cpu()
        self.model.cpu()
        return self

    def cuda(self, device=None):
        self._model.cuda(device=device)
        self.model.cuda(device=device)
        return self

    def zero_grad(self):
        self._model.zero_grad()

    def state_dict(self, destination=None, prefix='', keep_vars=False):
        return self._model.state_dict(destination=destination, prefix=prefix, keep_vars=keep_vars)

    def load_state_dict(self, state_dict, strict=True):
        return self._model.load_state_dict(state_dict, strict=strict)

    def parameters(self, recurse=True):
        return self._model.parameters(recurse=recurse)

    def named_parameters(self, prefix='', recurse=True):
        return self._model.named_parameters(prefix=prefix, recurse=recurse)

    def children(self):
        return self._model.children()

    def named_children(self):
        return self._model.named_children()

    def modules(self):
        return self._model.modules()

    def named_modules(self, memo=None, prefix=''):
        return self._model.named_modules(memo=memo, prefix=prefix)

    def apply(self, fn):
        return self._model.apply(fn)

    # ----------------------------------------------------------------------------------------- #

    def remove_misclassify(self, data, **kwargs):
        with torch.no_grad():
            _input, _label = self.get_data(data, **kwargs)
            _classification = self.get_class(_input)
            repeat_idx = _classification.eq(_label)
        return _input[repeat_idx], _label[repeat_idx]

    def generate_target(self, _input: torch.Tensor, idx=1, same=False) -> torch.LongTensor:

        if len(_input.shape) == 3:
            _input = _input.unsqueeze(0)
        self.batch_size = _input.size(0)
        with torch.no_grad():
            _output = self.get_logits(_input)
        target = _output.argsort(dim=-1, descending=True)[:, idx]
        if same:
            target = repeat_to_batch(target.mode(dim=0)[0], len(_input))
        return target