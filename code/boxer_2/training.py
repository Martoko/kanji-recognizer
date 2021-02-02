import argparse
import math
import os
import pathlib
import time
import random

import PIL
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import torchvision
import torchvision.transforms as transforms
import wandb

import kanji
from datasets import KanjiBoxerGeneratedDataset
from model import KanjiBoxer

cuda_is_available = torch.cuda.is_available() and torch.cuda.get_device_capability() != (3, 0) and \
                    torch.cuda.get_device_capability()[0] >= 3


def run(args):
    resume = args.resume
    if isinstance(resume, str) and "/" in resume:
        resume = resume.split("/")[-1]
    wandb.init(project="qanji", config=args, resume=resume, name=args.name)
    device = torch.device("cuda" if cuda_is_available else "cpu")

    def character_set_from_name(name):
        if name == "kanji":
            return kanji.kanji
        if name == "jouyou_kanji":
            return kanji.jouyou_kanji
        if name == "frequent_kanji":
            return kanji.frequent_kanji
        if name == "frequent_kanji_plus":
            return kanji.frequent_kanji_plus
        if name == "jouyou_kanji_and_simple_hiragana":
            return kanji.jouyou_kanji_and_simple_hiragana
        if name == "simple_hiragana":
            return kanji.simple_hiragana
        if name == "simpler_hiragana":
            return kanji.simpler_hiragana
        raise Exception(f"Unknown character set {name}")

    characters = character_set_from_name(args.character_set)

    def denormalize(img):
        return img / 0.28 + 0.63

    class GaussianNoise(object):
        def __init__(self, mean=0.0, std=0.1):
            self.std = std
            self.mean = mean

        def __call__(self, tensor):
            return tensor + torch.randn(tensor.size()) * self.std + self.mean

        def __repr__(self):
            return self.__class__.__name__ + '(mean={0}, std={1})'.format(self.mean, self.std)

    train_transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=(0.63, 0.63, 0.63), std=(0.28, 0.28, 0.28))
    ])

    test_transform = transforms.Compose([
        transforms.Resize(size=(32, 32)),
        transforms.ToTensor(),
        transforms.Normalize(mean=(0.63, 0.63, 0.63), std=(0.28, 0.28, 0.28))
    ])

    trainset = KanjiBoxerGeneratedDataset(args.fonts_folder, args.background_images_folder,
                                               noise_background_weight=args.bg_noise_weight,
                                               img_background_weight=args.bg_image_weight,
                                               plain_background_weight=args.bg_plain_weight,
                                               characters=characters,
                                               transform=train_transform)
    trainloader = torch.utils.data.DataLoader(trainset, batch_size=args.batch_size, pin_memory=cuda_is_available)
    wandb.config.update({"dataset": trainset.id})

    model = KanjiBoxer(output_dimensions=4).to(device)
    wandb.watch(model)
    if args.input_path is not None and os.path.exists(args.input_path):
        print(f"Loading checkpoint from {args.input_path}")
        model.load_state_dict(torch.load(args.input_path))

    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=args.learning_rate)

    def save_checkpoint():
        torch.save({
            "sample": sample,
            "model_state_dict": model.state_dict(),
            "optimizer_state_dict": optimizer.state_dict()
        }, os.path.join(wandb.run.dir, "checkpoint.ckpt"))

    running_loss = 0.0
    time_of_last_log = time.time()
    batches_since_last_report = 0
    begin_training_time = time.time()
    sample = 0
    if resume:
        print("Resuming from checkpoint...")
        wandb.restore("checkpoint.ckpt")
        checkpoint = torch.load(os.path.join(wandb.run.dir, "checkpoint.ckpt"))
        sample = checkpoint['sample']
        model.load_state_dict(checkpoint['model_state_dict'])
        optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
    for current_batch, data in enumerate(trainloader, 1):
        model.train()
        # get the inputs; datasets is a list of [inputs, labels]
        images, labels = data
        images = images.to(device)
        labels = labels.float().to(device)

        # zero the parameter gradients
        optimizer.zero_grad()

        # forward + backward + optimize
        outputs = model(images)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()

        # print statistics
        running_loss += loss.item()
        batches_since_last_report += 1
        sample += args.batch_size
        if time.time() - time_of_last_log > args.log_frequency:
            loss = running_loss / batches_since_last_report
            print(f"[{current_batch + 1:5d}] "
                  f"loss: {loss :.3f} ")
            images = images.cpu()
            labels = labels.cpu()
            wandb.log({
                "train/loss": loss,
                "batch": current_batch,
                "sample": sample
            }, commit=True)
            save_checkpoint()
            running_loss = 0.0
            batches_since_last_report = 0
            time_of_last_log = time.time()

            if time.time() - begin_training_time > args.training_time * 60:
                break

    print("Finished Training")

    def log_examples(count=10):
        with torch.no_grad():
            dataiter = iter(trainloader)
            images, labels = dataiter.next()
            images = images.to(device)
            labels = labels.to(device)

            outputs = model(images)
            _, predictions = torch.max(outputs, 1)
            images = images.cpu()
            labels = labels.cpu()
            predictions = predictions.cpu()

            # print images
            examples = min(count, args.batch_size)
            wandb.log({
                "train/examples": [wandb.Image(
                    image,
                    caption=f"Prediction: {trainset.characters[prediction]} Truth: {trainset.characters[label]}"
                ) for image, prediction, label in zip(images[:examples], predictions[:examples], labels[:examples])]
            })

    def log_failure_cases(count=10):
        with torch.no_grad():
            failure_cases = []
            correct = 0
            total = 0
            for data in trainloader:
                images, labels = data
                images = images.to(device)
                labels = labels.to(device)

                outputs = model(images)
                _, predictions = torch.max(outputs.data, 1)
                total += labels.size(0)
                correct += (predictions == labels).sum().item()

                for prediction, label, image, output in zip(predictions, labels, images, outputs):
                    if prediction != label:
                        failure_cases += [{
                            "image": image,
                            "prediction": testset.characters[prediction],
                            "label": testset.characters[label],
                            "confidence": output[label]
                        }]

                if total >= len(testset.characters):
                    break
            wandb.log({"train/failure_cases": [wandb.Image(
                case["image"],
                caption=f"Prediction: {case['prediction']} Truth: {case['label']}"
            ) for case in sorted(failure_cases, key=lambda item: item['confidence'])[:count]]})
            return 100 * correct / total

    log_examples()
    log_failure_cases()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train a model to recognize kanji.")
    parser.add_argument("-o", "--output-path", type=str, default="data/models/recognizer.pt",
                        help="save model to this path")
    parser.add_argument("-i", "--input-path", type=str, default=None,
                        help="load model from this path")
    parser.add_argument("-f", "--fonts-folder", type=str, default="data/fonts",
                        help="path to a folder containing fonts (default: data/fonts)")
    parser.add_argument("-e", "--test-folder", type=str, default="data/test",
                        help="path to a folder containing test images named after their label character (default: data/test)")
    parser.add_argument("-b", "--background-images-folder", type=str, default="data/background-images",
                        help="path to a folder containing background images (default: data/background-images)")
    parser.add_argument("-B", "--batch-size", type=int, default=128,
                        help="the size of the batch used on each training step (default: 128)")
    parser.add_argument("-t", "--training-time", type=float, default=float('inf'),
                        help="stop training after this amount of minutes (default: inf)")
    parser.add_argument("-l", "--learning-rate", type=float, default=1e-4,
                        help="the learning rate of the the optimizer (default: 1e-4)")
    parser.add_argument("-j", "--color-jitter", nargs='+', type=float, default=[0.1, 0.1, 0.1, 0.1],
                        help="brightness, contrast, saturation, hue passed onto the color jitter transform (default: 0.1, 0.1, 0.1, 0.1)")
    parser.add_argument("-N", "--noise", nargs='+', type=float, default=[0, 0.0007],
                        help="mean, std of gaussian noise transform (default: 0, 0.0007)")
    parser.add_argument("-c", "--character-set", type=str, default="frequent_kanji_plus",
                        help="name of characters to use (default: frequent_kanji_plus)")
    parser.add_argument("-F", "--log-frequency", type=float, default=600,
                        help="how many seconds between logging (default: 600)")
    parser.add_argument("-s", "--side-text-ratio", type=float, default=0.9,
                        help="generate artifacts in training images from text before/after (default: 0.9)")
    parser.add_argument("--bg-plain-weight", type=float, default=1,
                        help="weight of images whose background are a single color (default: 1)")
    parser.add_argument("--bg-noise-weight", type=float, default=3,
                        help="weight of images whose background consist of random noise (default: 3)")
    parser.add_argument("--bg-image-weight", type=float, default=5,
                        help="weight of images whose background is a random crop of a real image (default: 5)")
    parser.add_argument("-n", "--name", type=str,
                        help="name of the run (default: auto generated)")
    parser.add_argument("-r", "--resume", type=str, default=False,
                        help="resumes a previous run given a run id or run path")
    run(parser.parse_args())