import io
import sys
from typing import *
import torch
from PIL import Image, ImageDraw
from PIL.Image import NEAREST
from PySide6 import QtGui
import numpy as np
import PIL

from PySide6.QtGui import QGuiApplication, QCursor, QPixmap
from PySide6.QtWidgets import (QApplication, QLabel, QPushButton,
                               QVBoxLayout, QWidget)
from PySide6.QtCore import Slot, Qt, QEvent, QRect, QByteArray, QBuffer, QIODevice, QPoint
from matplotlib import pyplot
from torchvision.transforms import transforms

from box_model import KanjiBoxer
import kanji
from model import KanjiRecognizer


class Qanji(QWidget):
    def __init__(self) -> None:
        QWidget.__init__(self)

        # This hangs, show nice loading bar
        # self.ocr_reader = easyocr.Reader(['ja'], gpu=False)
        self.boxer = KanjiBoxer(input_dimensions=32)
        self.boxer.load_state_dict(torch.load('./box_saved_model.pt'))

        self.recog = KanjiRecognizer(input_dimensions=32, output_dimensions=len(kanji.Kanji.characters()))
        self.recog.load_state_dict(torch.load('./saved_model.pt'))

        self.setWindowTitle("Qanji")

        self.pixmap: Optional[QPixmap] = None

        self.button = QPushButton("Click me!")
        self.screenshot_label = QLabel()
        self.screenshot_label.setAlignment(Qt.AlignCenter)
        self.text = QLabel("While focus is on this window, press shift to perform OCR")
        self.text.setAlignment(Qt.AlignCenter)

        self.layout = QVBoxLayout()
        self.layout.addWidget(self.screenshot_label)
        self.layout.addWidget(self.text)
        self.layout.addWidget(self.button)
        self.setLayout(self.layout)

        # Connecting the signal
        # self.button.clicked.connect(self.new_screenshot)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.type() != QEvent.KeyPress:
            return
        if event.key() != Qt.Key_Shift:
            return
        self.shoot_screen()

    @Slot()
    def shoot_screen(self) -> None:
        self.pixmap = self.clip_around(QCursor.pos(), 32)
        if self.pixmap is None:
            return

        # ocr = self.ocr_reader.readtext(self.pixmap_to_bytes(self.pixmap))
        # self.text.setText("\n".join([value for _, value, _ in ocr]))

        scaled = self.pixmap
        img = self.pixmap.toImage()
        # img = Image.frombytes("RGB", [self.pixmap.width, self.pixmap.height], self.pixmap)
        # img
        np_array = np.empty((3, 32, 32), dtype=np.uint8)
        for x in range(0, 32):
            for y in range(0, 32):
                c = img.pixelColor(x, y)
                np_array[0, y, x] = c.getRgb()[0]
                np_array[1, y, x] = c.getRgb()[1]
                np_array[2, y, x] = c.getRgb()[2]
        tensors = transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))(
            transforms.ToTensor()(
                # np_array
                self.pixmap_to_pil(scaled)
            )
        )
        pilimg = self.pixmap_to_pil(scaled)
        box = self.boxer(tensors.reshape(-1, 3, 32, 32))
        box = (box.detach().numpy() * 32)[0]
        box[0] -= 1
        box[1] -= 1
        box[2] += 1
        box[3] += 1
        d = ImageDraw.Draw(pilimg)
        d.rectangle(box, outline='red')
        pyplot.imshow(pilimg)
        pyplot.show()

        pilimg = self.pixmap_to_pil(scaled)
        pilimg = pilimg.resize((32, 32), box=list(box), resample=NEAREST)
        pyplot.imshow(pilimg)
        pyplot.show()
        tensors = transforms.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))(
            transforms.ToTensor()(
                # np_array
                pilimg
            )
        )

        outputs = self.recog(tensors.reshape(-1, 3, 32, 32))
        _, predicted = torch.max(outputs, 1)
        ocr = chr(kanji.Kanji.characters()[predicted])
        print(ocr)
        self.text.setText(ocr)

        scaled_pixmap = scaled  # .scaled(
        #     self.screenshot_label.size(),
        #     Qt.KeepAspectRatio,
        #     Qt.SmoothTransformation
        # )
        self.screenshot_label.setPixmap(scaled_pixmap)

    @staticmethod
    def clip_around(point: QPoint, size: int) -> Optional[QPixmap]:
        screen = QGuiApplication.screenAt(point)
        screen_geometry = screen.geometry()
        clip_geometry = QRect(
            point.x() - size / 2, point.y() - size / 2,
            size, size
        )

        if clip_geometry.left() < screen_geometry.left():
            clip_geometry.moveLeft(screen_geometry.left())

        if clip_geometry.right() > screen_geometry.right():
            clip_geometry.moveRight(screen_geometry.right())

        if clip_geometry.top() < screen_geometry.top():
            clip_geometry.moveTop(screen_geometry.top())

        if clip_geometry.bottom() > screen_geometry.bottom():
            clip_geometry.moveBottom(screen_geometry.bottom())

        if not screen_geometry.contains(clip_geometry):
            print("Clip size is larger than screen size")
            return None

        return screen.grabWindow(
            0,
            x=clip_geometry.x(),
            y=clip_geometry.y(),
            w=clip_geometry.width(),
            h=clip_geometry.height()
        )

    @staticmethod
    def pixmap_to_bytes(pixmap: QPixmap) -> bytes:
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.WriteOnly)
        pixmap.save(buffer, "PNG")
        return byte_array.data()

    @staticmethod
    def pixmap_to_pil(pixmap: QPixmap) -> PIL.Image.Image:
        return Image.open(io.BytesIO(Qanji.pixmap_to_bytes(pixmap)))


if __name__ == "__main__":
    app = QApplication(sys.argv)

    widget = Qanji()
    widget.show()

    sys.exit(app.exec_())