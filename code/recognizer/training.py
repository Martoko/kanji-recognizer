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
from datasets import RecognizerGeneratedDataset, RecognizerTestDataset
from model import KanjiRecognizer

cuda_is_available = torch.cuda.is_available() and torch.cuda.get_device_capability() != (3, 0) and \
                    torch.cuda.get_device_capability()[0] >= 3


def run(args):
    wandb.init(project="qanji", config=args)
    device = torch.device("cuda" if cuda_is_available else "cpu")

    def character_set_from_name(name):
        if name == "jouyou_kanji":
            return kanji.jouyou_kanji
        if name == "jouyou_kanji_and_simple_hiragana":
            return kanji.jouyou_kanji_and_simple_hiragana
        raise Exception(f"Unknown character set {name}")

    characters = character_set_from_name(args.character_set)

    def denormalize(img):
        return img / 0.5 + 0.5

    def imshow(img):
        npimg = denormalize(img).numpy()
        plt.imshow(np.transpose(npimg, (1, 2, 0)))
        plt.show()

    class GaussianNoise(object):
        def __init__(self, mean=0.0, std=0.1):
            self.std = std
            self.mean = mean

        def __call__(self, tensor):
            return tensor + torch.randn(tensor.size()) * self.std + self.mean

        def __repr__(self):
            return self.__class__.__name__ + '(mean={0}, std={1})'.format(self.mean, self.std)

    train_transform = transforms.Compose([
        transforms.RandomCrop((32, 32)),
        transforms.ColorJitter(*args.color_jitter),
        transforms.Lambda(lambda img: PIL.ImageOps.invert(img) if random.random() > 0.5 else img),
        transforms.ToTensor(),
        GaussianNoise(*args.noise),
        transforms.Normalize(mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5))
    ])

    test_transform = transforms.Compose([
        transforms.Resize(size=(32, 32), interpolation=PIL.Image.NEAREST),
        transforms.ToTensor(),
        transforms.Normalize(mean=(0.5, 0.5, 0.5), std=(0.5, 0.5, 0.5))
    ])

    trainset = RecognizerGeneratedDataset(args.fonts_folder, args.background_images_folder, characters=characters,
                                          transform=train_transform)
    trainloader = torch.utils.data.DataLoader(trainset, batch_size=args.batch_size)
    testset = RecognizerTestDataset(args.test_folder, characters=characters, transform=test_transform)
    testloader = torch.utils.data.DataLoader(testset, batch_size=args.batch_size)
    wandb.config.update({"dataset": trainset.id})

    model = KanjiRecognizer(input_dimensions=32, output_dimensions=len(trainset.characters)).to(device)
    wandb.watch(model)
    if args.input_path is not None and os.path.exists(args.input_path):
        print(f"Loading checkpoint from {args.input_path}")
        model.load_state_dict(torch.load(args.input_path))

    def evaluate_train():
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
            ) for case in sorted(failure_cases, key=lambda item: item['confidence'])[:8]]})
            return 100 * correct / total

    def evaluate_validation():
        with torch.no_grad():
            test_results = []
            failure_cases = []
            correct = 0
            total = 0
            for data in testloader:
                images, labels = data
                images = images.to(device)
                labels = labels.to(device)

                outputs = model(images)
                _, predictions = torch.max(outputs.data, 1)
                total += labels.size(0)
                correct += (predictions == labels).sum().item()

                for prediction, label, image, output in zip(predictions, labels, images, outputs):
                    test_results += [{
                        "image": image,
                        "prediction": testset.characters[prediction],
                        "label": testset.characters[label],
                        "confidence": output[label]
                    }]
                    if prediction != label:
                        failure_cases += [{
                            "image": image,
                            "prediction": testset.characters[prediction],
                            "label": testset.characters[label],
                            "confidence": output[label]
                        }]

                if total >= len(testset.characters):
                    break
            wandb.log({
                "validation/examples": [wandb.Image(
                    case["image"],
                    caption=f"Prediction: {case['prediction']} Truth: {case['label']}"
                ) for case in test_results[:20]],
                "validation/failure_cases": [wandb.Image(
                    case["image"],
                    caption=f"Prediction: {case['prediction']} Truth: {case['label']}"
                ) for case in sorted(failure_cases, key=lambda item: item['confidence'])[:8]]
            })
            return 100 * correct / total

    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=args.learning_rate)

    running_loss = 0.0
    time_of_last_report = time.time()
    log_frequency = 20  # report every X seconds
    batches_since_last_report = 0
    begin_training_time = time.time()
    for current_batch, data in enumerate(trainloader, 1):
        # get the inputs; datasets is a list of [inputs, labels]
        images, labels = data
        images = images.to(device)
        labels = labels.to(device)

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
        if time.time() - time_of_last_report > log_frequency:
            loss = running_loss / batches_since_last_report
            train_accuracy = evaluate_train()
            validation_accuracy = evaluate_validation()
            print(f"[{current_batch + 1:5d}] "
                  f"loss: {loss :.3f} "
                  f"train/accuracy: {train_accuracy :.3f} "
                  f"validation/accuracy {validation_accuracy :.3f}")
            _, predictions = torch.max(outputs, 1)
            images = images.cpu()
            labels = labels.cpu()
            predictions = predictions.cpu()
            wandb.log({
                "train/loss": loss,
                "train/accuracy": train_accuracy,
                "validation/accuracy": validation_accuracy,
                "batch": current_batch,
                "sample": current_batch * args.batch_size,
                "train/examples": [wandb.Image(
                    image,
                    caption=f"Prediction: {trainset.characters[prediction]} Truth: {trainset.characters[label]}"
                ) for image, label, prediction in zip(images[:8], predictions[:8], labels[:8])]
            })
            running_loss = 0.0
            batches_since_last_report = 0
            time_of_last_report = time.time()

            if time.time() - begin_training_time > args.training_time * 60:
                break

    print("Finished Training")

    if args.output_path is not None:
        # TODO: Save a full checkpoint, allow resuming, this includes saving batch/samples processed so far
        print(f"Saving model to {args.output_path}")
        pathlib.Path(args.output_path).parent.mkdir(parents=True, exist_ok=True)
        torch.save(model.state_dict(), args.output_path)

    print("Accuracy of the network: %d %%" % evaluate_train())

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
                "examples": [wandb.Image(
                    image,
                    caption=f"Prediction: {trainset.characters[prediction]} Truth: {trainset.characters[label]}"
                ) for image, label, prediction in zip(images[:examples], predictions[:examples], labels[:examples])]
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
    parser.add_argument("-t", "--training-time", type=float, default=10,
                        help="amount of minutes to train the network (default: 10)")
    parser.add_argument("-l", "--learning-rate", type=float, default=1e-3,
                        help="the learning rate of the the optimizer (default: 1e-3)")
    parser.add_argument("-j", "--color-jitter", nargs='+', type=float, default=[0.1, 0.1, 0.1, 0.1],
                        help="brightness, contrast, saturation, hue passed onto the color jitter transform (default: 0.1, 0.1, 0.1, 0.1)")
    parser.add_argument("-n", "--noise", nargs='+', type=float, default=[0, 0.0001],
                        help="mean, std of gaussian noise transform (default: 0, 0.0001)")
    parser.add_argument("-c", "--character-set", type=str, default="jouyou_kanji_and_simple_hiragana",
                        help="name of characters to use (default: jouyou_kanji_and_simple_hiragana)")
    run(parser.parse_args())