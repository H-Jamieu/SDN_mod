import os
import time
import datetime
import numpy as np
import pandas as pd

import torch
import torch.nn.functional as F
from torch.autograd import Variable
from torch.cuda.amp import autocast
import torchvision.transforms as transforms
from torch.utils.data import Dataset
from PIL import Image


def getTime():
    time_stamp = datetime.datetime.now()
    return time_stamp.strftime('%H:%M:%S')


class AverageMeter(object):
    """Computes and stores the average and current value"""

    def __init__(self, name, fmt=':f'):
        self.name = name
        self.fmt = fmt
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count

    def __str__(self):
        fmtstr = '{name} {avg' + self.fmt + '}'
        return fmtstr.format(**self.__dict__)


class ProgressMeter(object):
    def __init__(self, num_batches, meters, prefix=""):
        self.batch_fmtstr = self._get_batch_fmtstr(num_batches)
        self.meters = meters
        self.prefix = prefix

    def display(self, batch):
        entries = [self.prefix + self.batch_fmtstr.format(batch)]
        entries += [str(meter) for meter in self.meters]
        print('\t'.join(entries))

    def _get_batch_fmtstr(self, num_batches):
        num_digits = len(str(num_batches // 1))
        fmt = '{:' + str(num_digits) + 'd}'
        return '[' + fmt + '/' + fmt.format(num_batches) + ']'


def accuracy(output, target, topk=(1,)):
    with torch.no_grad():
        maxk = max(topk)
        batch_size = target.size(0)

        _, pred = output.topk(maxk, 1, True, True)
        pred = pred.t()
        correct = pred.eq(target.view(1, -1).expand_as(pred))

        res = []
        for k in topk:
            correct_k = correct[:k].reshape(-1).float().sum(0, keepdim=True)
            res.append(correct_k.mul_(100.0 / batch_size))
        return res


def train(model, train_loader, optimizer, ceriation, epoch, amp=False):
    batch_time = AverageMeter('Time', ':6.2f')
    data_time = AverageMeter('Data', ':6.2f')
    losses = AverageMeter('Loss', ':6.2f')
    top1 = AverageMeter('Acc@1', ':6.2f')
    progress = ProgressMeter(len(train_loader), [batch_time, data_time, losses, top1], prefix=getTime() + " Train Epoch: [{}]".format(epoch + 1))
    scaler = torch.cuda.amp.GradScaler()

    model.train()
    end = time.time()
    for i, (images, labels) in enumerate(train_loader):
        # measure data loading time
        data_time.update(time.time() - end)
        images = images.cuda(non_blocking=True)
        labels = labels.cuda(non_blocking=True)

        if amp:
            with autocast():
                logist = model(images)
                loss = ceriation(logist, labels)

            optimizer.zero_grad()
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
        else:
            logist = model(images)
            loss = ceriation(logist, labels)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        acc1, acc5 = accuracy(logist, labels, topk=(1, 5))
        losses.update(loss.item(), images[0].size(0))
        top1.update(acc1[0], images[0].size(0))
        batch_time.update(time.time() - end)
        end = time.time()

    progress.display(0)
    return losses.avg, top1.avg.to("cpu", torch.float).item()


def evaluate(model, eva_loader, ceriation, prefix, ignore=-1):
    losses = AverageMeter('Loss', ':3.2f')
    top1 = AverageMeter('Acc@1', ':3.2f')
    model.eval()

    with torch.no_grad():
        for i, (images, labels) in enumerate(eva_loader):
            images = images.cuda()
            labels = labels.cuda()

            logist = model(images)

            loss = ceriation(logist, labels)
            acc1, acc5 = accuracy(logist, labels, topk=(1, 5))

            losses.update(loss.item(), images[0].size(0))
            top1.update(acc1[0], images[0].size(0))

    if prefix != "":
        print(getTime(), prefix, round(top1.avg.item(), 2))

    return losses.avg, top1.avg.to("cpu", torch.float).item()


def evaluateWithBoth(model1, model2, eva_loader, prefix):
    model1.eval()
    model2.eval()
    top1 = AverageMeter('Acc@1', ':3.2f')

    with torch.no_grad():
        for i, (images, labels) in enumerate(eva_loader):
            images = images.cuda()
            labels = labels.cuda()

            logist1 = model1(images)
            logist2 = model2(images)
            logist = (F.softmax(logist1, dim=1) + F.softmax(logist2, dim=1)) / 2
            acc1, acc5 = accuracy(logist, labels, topk=(1, 5))
            top1.update(acc1[0], images[0].size(0))

    if prefix != "":
        print(getTime(), prefix, round(top1.avg.item(), 2))

    return top1.avg.to("cpu", torch.float).item()


def predict(predict_loader, model):
    model.eval()
    preds = []
    probs = []

    with torch.no_grad():
        for images, _, _ in predict_loader:
            if torch.cuda.is_available():
                images = Variable(images).cuda()
                logits = model(images)
                outputs = F.softmax(logits, dim=1)
                prob, pred = torch.max(outputs.data, 1)
                preds.append(pred)
                probs.append(prob)

    return torch.cat(preds, dim=0).cpu(), torch.cat(probs, dim=0).cpu()


def predict_softmax(predict_loader, model):
    model.eval()
    softmax_outs = []
    with torch.no_grad():
        for images1, images2 in predict_loader:
            if torch.cuda.is_available():
                images1 = images1.cuda()
                images2 = images2.cuda()

            logits1 = model(images1)
            logits2 = model(images2)
            outputs = (F.softmax(logits1, dim=1) + F.softmax(logits2, dim=1)) / 2
            softmax_outs.append(outputs)

    return torch.cat(softmax_outs, dim=0).cpu()


def predict_repre(predict_loader, model):
    model.eval()    # Change model to 'eval' mode.
    repres = []
    with torch.no_grad():
        for images, _ in predict_loader:
            images = images.cuda()
            outputs = model(images)
            repres.append(outputs)

    repres = torch.cat(repres, dim=0)
    return repres.detach().cpu().numpy()


class Clothing1M_Dataset(Dataset):
    def __init__(self, data, labels, root_dir, transform=None, target_transform=None):
        self.data = np.array(data)
        self.targets = np.array(labels)
        self.root_dir = root_dir
        self.length = len(self.targets)

        if transform is None:
            self.transform = transforms.ToTensor()
        else:
            self.transform = transform

        self.target_transform = target_transform

    def __getitem__(self, index):
        img_paths, target = self.data[index], self.targets[index]

        img_paths = os.path.join(self.root_dir, img_paths)
        img = Image.open(img_paths).convert('RGB')

        if self.transform is not None:
            img = self.transform(img)

        if self.target_transform is not None:
            target = self.target_transform(target)

        return img, target

    def __len__(self):
        return self.length

    def getData(self):
        return self.data, self.targets
    
class Noisy_ostracods(Dataset):
    def __init__(self, data, labels, transform=None):
        self.data = data
        self.fixed_image_base_path = '/mnt/x/class_images' #'/mnt/e/data/ostracods_id/class_images'
        self.transform = transform
        self.labels = labels
        self.root_dir = self.fixed_image_base_path
    
    def __getitem__(self, index):
        img_paths, target = self.data[index], self.labels[index]

        img_paths = os.path.join(self.root_dir, img_paths)
        img = Image.open(img_paths).convert('RGB')

        if self.transform is not None:
            img = self.transform(img)

        return img, target
    
    def get_plain_item(self, idx):
        img_path = os.path.join(self.fixed_image_base_path, self.img_labels.iloc[idx, 0])
        image = Image.open(img_path)
        label = self.img_labels.iloc[idx, 1]
        return image, label

    def __len__(self):
        return len(self.data)
    
class Noisy_ostracods_unlabeled(Dataset):
    def __init__(self, train, transform=None):
        self.fixed_annotation_path = f'/mnt/d/Noisy_ostracods/datasets/ostracods_genus_final_{train}.csv'
        self.fixed_image_base_path = '/mnt/x/class_images' #'/mnt/e/data/ostracods_id/class_images'
        self.transform = transform
        self.train = train
        self.img_labels = pd.read_csv(self.fixed_annotation_path, header=None)
        # transform img_labels[,1] to a 1-d array of np-int8 as labels
        self.targets = self.img_labels[1].values.astype(np.int8)
        self.root_dir = self.fixed_image_base_path
    
    def __getitem__(self, idx):
        img_path = os.path.join(self.fixed_image_base_path, self.img_labels.iloc[idx, 0])
        image = Image.open(img_path)
        img2 = self.transform(image)
        image = self.transform(image)
        return image, img2
    

    def __len__(self):
        return len(self.img_labels)


class Clothing1M_Unlabeled_Dataset(Dataset):
    def __init__(self, data, root_dir, transform=None):
        self.train_data = np.array(data)
        self.root_dir = root_dir
        self.length = len(self.train_data)

        if transform is None:
            self.transform = transforms.ToTensor()
        else:
            self.transform = transform

    def __getitem__(self, index):
        img_paths = self.train_data[index]
        img_paths = os.path.join(self.root_dir, img_paths)
        img = Image.open(img_paths).convert('RGB')

        if self.transform is not None:
            img1 = self.transform(img)
            img2 = self.transform(img)

        return img1, img2

    def __len__(self):
        return self.length