import argparse
import os

import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import torchvision
import torchvision.transforms as transforms

import datasets
from model import KanjiRecognizer


def imshow(img):
    img = img / 2 + 0.5  # unnormalize
    npimg = img.numpy()
    plt.imshow(np.transpose(npimg, (1, 2, 0)))
    plt.show()


def run(args):
    transform = transforms.Compose([transforms.ToTensor(), transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])

    trainset = datasets.Kanji(args.font_file, args.background_images_folder, party_mode=True, transform=transform)
    trainloader = torch.utils.data.DataLoader(trainset, batch_size=128)
    testset = datasets.Kanji(args.font_file, args.background_images_folder, party_mode=True, transform=transform)
    testloader = torch.utils.data.DataLoader(testset, batch_size=128)

    # # get some random training images
    # dataiter = iter(trainloader)
    # images, labels = dataiter.next()
    #
    # # show images
    # imshow(torchvision.utils.make_grid(images))
    # # print labels
    # print(' '.join('%5s' % classes[labels[j]] for j in range(4)))

    PATH = './cifar_net.pt'
    model = KanjiRecognizer(input_dimensions=32, output_dimensions=len(trainset.characters()))
    if os.path.exists(PATH):
        model.load_state_dict(torch.load(PATH))

    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=1e-3)

    epoch_length = 100
    running_loss = 0.0
    for i, data in enumerate(trainloader, 0):
        # get the inputs; datasets is a list of [inputs, labels]
        inputs, labels = data

        # zero the parameter gradients
        optimizer.zero_grad()

        # forward + backward + optimize
        outputs = model(inputs)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()

        # print statistics
        running_loss += loss.item()
        if i % epoch_length == (epoch_length - 1):  # print every 20 mini-batches
            print(f'[{i + 1:5d}] loss: {running_loss / epoch_length:.3f}')
            running_loss = 0.0

        if i >= 2000:
            break

    print('Finished Training')

    torch.save(model.state_dict(), PATH)

    model = KanjiRecognizer(input_dimensions=32, output_dimensions=len(trainset.characters()))
    model.load_state_dict(torch.load(PATH))

    correct = 0
    total = 0
    with torch.no_grad():
        for data in trainloader:
            images, labels = data
            outputs = model(images)
            _, predicted = torch.max(outputs.data, 1)
            total += labels.size(0)
            correct += (predicted == labels).sum().item()

            if total >= 1000:
                break

    print('Accuracy of the network on the 1000 test images: %d %%' % (100 * correct / total))

    dataiter = iter(trainloader)
    images, labels = dataiter.next()

    outputs = model(images)
    _, predicted = torch.max(outputs, 1)

    print('GroundTruth:', ' '.join('%5s' % chr(trainset.characters()[labels[j]]) for j in range(10)))
    print('Predicted:\t', ' '.join('%5s' % chr(trainset.characters()[predicted[j]]) for j in range(10)))

    print("########### PARTY MODE OOOOOOOOOOOOOOOOOOOOOOOOONNNNNN!! ###########")

    # print images
    imshow(torchvision.utils.make_grid(images[:10]))

    dataiter = iter(testloader)
    images, labels = dataiter.next()

    outputs = model(images)
    _, predicted = torch.max(outputs, 1)

    print('GroundTruth:', ' '.join('%5s' % chr(trainset.characters()[labels[j]]) for j in range(10)))
    print('Predicted:\t', ' '.join('%5s' % chr(trainset.characters()[predicted[j]]) for j in range(10)))

    # print images
    imshow(torchvision.utils.make_grid(images[:10]))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate images with kanji on them.')
    parser.add_argument('-c', '--character-count', type=int, default=0,
                        help='amount of characters to generate')
    parser.add_argument('-s', '--repetition-count', type=int, default=10,
                        help='amount of sets to generate for each character')
    parser.add_argument('-o', '--output-path', type=str, default='generated',
                        help='path to the folder where generated images are going to be saved')
    parser.add_argument('-f', '--font-file', type=str, default='/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc',
                        help='path to font to use')
    parser.add_argument('-b', '--background-images-folder', type=str,
                        help='path to a folder containing background images')
    args = parser.parse_args()

    run(args)
