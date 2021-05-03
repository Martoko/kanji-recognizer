import glob
import math
import os
import pathlib
import random
from random import randint
from typing import *

import numpy as np
from PIL import Image, ImageFile, ImageDraw, ImageFilter
from torch.utils.data import IterableDataset
from torch.utils.data.dataset import T_co

from recognizer.data import character_sets, fonts

ImageFile.LOAD_TRUNCATED_IMAGES = True


def background_images(folder):
    return [
        Image.open(os.path.join(folder, name))
        for name in os.listdir(folder)
        if os.path.isfile(os.path.join(folder, name))
    ]


def random_color():
    return randint(0, 255), randint(0, 255), randint(0, 255)


WHITE_COLOR = (255, 255, 255)
BLACK_COLOR = (0, 0, 0)


def random_noise(width, height):
    return Image.fromarray(np.random.randint(0, 255, (width, height, 3), dtype=np.dtype('uint8')))


def draw_outlined_text(drawing, xy, *args, outline_fill=None, outline_thickness=None, fill, **kwargs):
    if outline_fill is None:
        outline_fill = random_color()
    if outline_thickness is None:
        outline_thickness = 1 + round(abs(np.random.normal(1)))

    x, y = xy
    for dx in range(-outline_thickness, 1 + outline_thickness):
        for dy in range(-outline_thickness, 1 + outline_thickness):
            drawing.text((x + dx, y + dy), *args, fill=outline_fill, **kwargs)

    drawing.text(xy, *args, fill=fill, **kwargs)


def draw_underlined_text(drawing, xy, text, *args, font, anchor, language, **kwargs):
    left, top, right, bottom = font.getbbox(text, anchor=anchor, language=language)

    width = round(abs(np.random.normal(1, 0.1)))
    jitter = np.random.normal(1, 0.1) * (font.size / 20)
    drawing.text(xy, text, *args, font=font, anchor=anchor, language=language, **kwargs)
    x, y = xy
    drawing.line((
        (x + left, y + bottom + jitter),
        (x + right, y + bottom + jitter)
    ), *args, width=width, **kwargs)


def eat_sides(image, left, right, top, bottom):
    left = round(left)
    right = round(right)
    top = round(top)
    bottom = round(bottom)
    color = random_color()

    drawing = ImageDraw.Draw(image)
    drawing.rectangle((
        (0, 0),
        (random.randint(0, left), image.height)
    ), fill=color)

    drawing.rectangle((
        (image.width, 0),
        (random.randint(right, image.width), image.height)
    ), fill=color)

    drawing.rectangle((
        (0, 0),
        (image.width, random.randint(0, top))
    ), fill=color)

    drawing.rectangle((
        (0, image.height),
        (image.width, random.randint(bottom, image.height))
    ), fill=color)


class RecognizerTrainingDataset(IterableDataset):
    def __init__(self, data_folder: str,
                 character_set: List[str], transform=None):
        super().__init__()
        fonts_folder = os.path.join(data_folder, "fonts")
        background_images_folder = os.path.join(data_folder, "backgrounds")
        self.font_infos = fonts.font_infos_in_folder(fonts_folder, character_set)
        self.transform = transform
        self.characters = character_set
        self.background_images = [
            Image.open(os.path.join(background_images_folder, name))
            for name in os.listdir(background_images_folder)
            if os.path.isfile(os.path.join(background_images_folder, name))
        ]
        self.stage = 0

    def fonts_supporting_glyph(self, glyph):
        return [font for font in self.font_infos if glyph in font.supported_glyphs]

    def random_background_image(self, width, height):
        background = random.choice(self.background_images)

        bg_left = randint(0, background.width - 2)
        bg_right = randint(bg_left + 1, background.width)
        bg_top = randint(0, background.height - 2)
        bg_bottom = randint(bg_top + 1, background.height)

        return background.resize(
            (width, height), box=(bg_left, bg_top, bg_right, bg_bottom)
        )

    def generate_background(self, width, height):
        choice = random.choices(["noise", "img", "plain"])[0]

        if choice == "noise":
            if random.random() > 0.5:
                image = self.random_background_image(width, height)
            else:
                image = Image.new('RGB', (width, height), color=random_color())
            return Image.blend(image, random_noise(width, height), min(abs(np.random.normal(0, 0.3)), 1))
        elif choice == "img":
            return self.random_background_image(width, height)
        else:
            return Image.new('RGB', (width, height), color=random_color())

    @staticmethod
    def generate_region_score(width: int, height: int, top_left: (int, int), bottom_right: (int, int)) -> Image:
        region_score = Image.new('L', (width // 2, height // 2), color=(0,))
        region_score_drawing = ImageDraw.Draw(region_score)
        region_score_drawing.rectangle(
            (
                (top_left[0] // 2, top_left[1] // 2),
                (bottom_right[0] // 2, bottom_right[1] // 2)
            ),
            fill=(255,)
        )
        # region_score_drawing.ellipse((top_left/2, bottom_right/2), fill=(255,))
        return region_score

    @staticmethod
    def generate_only_char(*args, **kwargs):
        region_score = Image.new('L', (128, 128), color=(0,))
        kwargs['fill'] = (255,)
        drawing = ImageDraw.Draw(region_score)
        drawing.text(*args, **kwargs)
        # return region_score.filter(ImageFilter.MaxFilter(3))
        return region_score

    @staticmethod
    def random_font_size():
        return int(random.choices([
            np.random.normal(15, 3),
            np.random.normal(20, 3),
            np.random.normal(35, 3),
            np.random.normal(50, 3)
        ], weights=[10, 3, 1, 1])[0])

    # Generates very simple fixed size characters black on white
    def generate_stage_0(self):
        label = random.randrange(0, len(self.characters))
        character = self.characters[label]
        font_info = self.fonts_supporting_glyph(character)[0]
        font_size = 32
        font = font_info.get(font_size)

        _, _, width, height = font.getbbox(character, anchor='lt', language='ja')
        sample = Image.new('RGB', (128, 128), color=WHITE_COLOR)
        drawing = ImageDraw.Draw(sample)
        drawing.text((64, 64), character, font=font, fill=BLACK_COLOR, anchor='mm', language='ja')

        region_score = self.generate_region_score(
            128, 128,
            top_left=(64 - width / 2, 64 - height / 2),
            bottom_right=(64 + width / 2, 64 + height / 2),
        )
        region_score = self.generate_only_char((64, 64), character, font=font, fill=BLACK_COLOR, anchor='mm',
                                               language='ja')

        if self.transform is None:
            return sample, label, region_score
        else:
            return self.transform(sample), label, self.transform(region_score)

    # 50/50 chance between black on white and white on black
    def generate_stage_1(self):
        label = random.randrange(0, len(self.characters))
        character = self.characters[label]
        font_info = self.fonts_supporting_glyph(character)[0]
        font_size = 32
        font = font_info.get(font_size)
        inverted = random.random() > 0.5

        _, _, width, height = font.getbbox(character, anchor='lt', language='ja')
        sample = Image.new('RGB', (128, 128), color=BLACK_COLOR if inverted else WHITE_COLOR)
        drawing = ImageDraw.Draw(sample)
        drawing.text((64, 64), character, font=font, fill=WHITE_COLOR if inverted else BLACK_COLOR, anchor='mm',
                     language='ja')

        region_score = self.generate_region_score(
            128, 128,
            top_left=(64 - width / 2, 64 - height / 2),
            bottom_right=(64 + width / 2, 64 + height / 2),
        )
        region_score = self.generate_only_char((64, 64), character, font=font,
                                               fill=WHITE_COLOR if inverted else BLACK_COLOR, anchor='mm',
                                               language='ja')

        if self.transform is None:
            return sample, label, region_score
        else:
            return self.transform(sample), label, self.transform(region_score)

    # Colors are now random
    def generate_stage_2(self):
        label = random.randrange(0, len(self.characters))
        character = self.characters[label]
        font_info = self.fonts_supporting_glyph(character)[0]
        font_size = 32
        font = font_info.get(font_size)

        _, _, width, height = font.getbbox(character, anchor='lt', language='ja')
        sample = Image.new('RGB', (128, 128), color=random_color())
        drawing = ImageDraw.Draw(sample)
        drawing.text((64, 64), character, font=font, fill=random_color(), anchor='mm', language='ja')

        region_score = self.generate_region_score(
            128, 128,
            top_left=(64 - width / 2, 64 - height / 2),
            bottom_right=(64 + width / 2, 64 + height / 2),
        )
        region_score = self.generate_only_char((64, 64), character, font=font, fill=random_color(), anchor='mm',
                                               language='ja')

        if self.transform is None:
            return sample, label, region_score
        else:
            return self.transform(sample), label, self.transform(region_score)

    # Font sizes can now vary between two sizes
    def generate_stage_3(self):
        label = random.randrange(0, len(self.characters))
        character = self.characters[label]
        font_info = self.fonts_supporting_glyph(character)[0]
        font_size = random.choice([20, 32])
        font = font_info.get(font_size)

        _, _, width, height = font.getbbox(character, anchor='lt', language='ja')
        sample = Image.new('RGB', (128, 128), color=random_color())
        drawing = ImageDraw.Draw(sample)
        drawing.text((64, 64), character, font=font, fill=random_color(), anchor='mm', language='ja')

        region_score = self.generate_region_score(
            128, 128,
            top_left=(64 - width / 2, 64 - height / 2),
            bottom_right=(64 + width / 2, 64 + height / 2),
        )
        region_score = self.generate_only_char((64, 64), character, font=font, fill=random_color(), anchor='mm',
                                               language='ja')

        if self.transform is None:
            return sample, label, region_score
        else:
            return self.transform(sample), label, self.transform(region_score)

    # Completely random font size, and random font
    def generate_stage_4(self):
        label = random.randrange(0, len(self.characters))
        character = self.characters[label]
        font_info = random.choice(self.fonts_supporting_glyph(character))
        font_size = self.random_font_size()
        font_size = max(8, font_size)
        font = font_info.get(font_size)

        _, _, width, height = font.getbbox(character, anchor='lt', language='ja')
        sample = Image.new('RGB', (128, 128), color=random_color())
        drawing = ImageDraw.Draw(sample)
        drawing.text((64, 64), character, font=font, fill=random_color(), anchor='mm', language='ja')

        region_score = self.generate_region_score(
            128, 128,
            top_left=(64 - width / 2, 64 - height / 2),
            bottom_right=(64 + width / 2, 64 + height / 2),
        )
        region_score = self.generate_only_char((64, 64), character, font=font, fill=random_color(), anchor='mm',
                                               language='ja')

        if self.transform is None:
            return sample, label, region_score
        else:
            return self.transform(sample), label, self.transform(region_score)

    # Random character location (while making sure at least part of the character is still in the center)
    def generate_stage_5(self):
        label = random.randrange(0, len(self.characters))
        character = self.characters[label]
        font_info = random.choice(self.fonts_supporting_glyph(character))
        font_size = self.random_font_size()
        font_size = max(8, font_size)
        font = font_info.get(font_size)

        _, _, width, height = font.getbbox(character, anchor='lt', language='ja')
        x_offset = int(((width / 2) - random.random() * width) * 0.8)
        y_offset = int(((height / 2) - random.random() * height) * 0.8)

        sample = Image.new('RGB', (128, 128), color=random_color())
        drawing = ImageDraw.Draw(sample)
        drawing.text((64 + x_offset, 64 + y_offset), character, font=font, fill=random_color(), anchor='mm',
                     language='ja')

        region_score = self.generate_region_score(
            128, 128,
            top_left=(64 + x_offset - width / 2, 64 + y_offset - height / 2),
            bottom_right=(64 + x_offset + width / 2, 64 + y_offset + height / 2),
        )
        region_score = self.generate_only_char((64 + x_offset, 64 + y_offset), character, font=font,
                                               fill=random_color(), anchor='mm',
                                               language='ja')

        if self.transform is None:
            return sample, label, region_score
        else:
            return self.transform(sample), label, self.transform(region_score)

    # Characters before and after, simulating a sentence
    def generate_stage_6(self):
        label = random.randrange(0, len(self.characters))
        character = self.characters[label]
        font_info = random.choice(self.fonts_supporting_glyph(character))
        font_size = self.random_font_size()
        font_size = max(8, font_size)
        font = font_info.get(font_size)

        before_count = random.randint(0, 10)
        after_count = random.randint(0, 10)
        total_count = before_count + after_count + 1
        before = [random.choice(tuple(font_info.supported_glyphs)) for _ in range(before_count)]
        after = [random.choice(tuple(font_info.supported_glyphs)) for _ in range(after_count)]
        text = ''.join(before) + character + ''.join(after)

        for extra_character in list(text):
            left, top, right, bottom = font.getbbox(extra_character, anchor='lt', language='ja')
            if right == 0 or bottom == 0:
                print(f"'{extra_character}' is missing from {os.path.basename(font_info['path'])}")
                exit(-1)

        left, top, right, bottom = font.getbbox(text, anchor='lt', language='ja')

        character_width = right / total_count
        character_height = bottom
        x = 64 - character_width / 2 - character_width * before_count
        x_offset = int(((character_width / 2) - random.random() * character_width) * 0.8)
        y_offset = int(((character_height / 2) - random.random() * character_height) * 0.8)

        sample = Image.new('RGB', (128, 128), color=random_color())
        drawing = ImageDraw.Draw(sample)
        drawing.text((x + x_offset, 64 + y_offset), text, font=font, fill=random_color(), anchor='lm', language='ja')

        region_score = self.generate_region_score(
            128, 128,
            top_left=(64 + x_offset - character_width / 2, 64 + y_offset - character_width / 2),
            bottom_right=(64 + x_offset + character_width / 2, 64 + y_offset + character_width / 2),
        )
        region_score = self.generate_only_char((64 + x_offset, 64 + y_offset), character, font=font,
                                               fill=random_color(),
                                               anchor='mm', language='ja')

        if self.transform is None:
            return sample, label, region_score
        else:
            return self.transform(sample), label, self.transform(region_score)

    # Borders, cropping the sides of the images, real images used as background with gaussian noise
    def generate_stage_7(self):
        label = random.randrange(0, len(self.characters))
        character = self.characters[label]
        font_info = random.choice(self.fonts_supporting_glyph(character))
        font_size = self.random_font_size()
        font_size = max(8, font_size)
        font = font_info.get(font_size)

        before_count = random.randint(0, 10)
        after_count = random.randint(0, 10)
        total_count = before_count + after_count + 1
        before = [random.choice(tuple(font_info.supported_glyphs)) for _ in range(before_count)]
        after = [random.choice(tuple(font_info.supported_glyphs)) for _ in range(after_count)]
        text = ''.join(before) + character + ''.join(after)

        for extra_character in list(text):
            left, top, right, bottom = font.getbbox(extra_character, anchor='lt', language='ja')
            if right == 0 or bottom == 0:
                print(f"{extra_character} is missing from {os.path.basename(font_info.path)}")
                exit(-1)

        left, top, right, bottom = font.getbbox(text, anchor='lt', language='ja')

        character_width = right / total_count
        character_height = bottom
        x = 64 - character_width / 2 - character_width * before_count
        x_offset = int(((character_width / 2) - random.random() * character_width) * 0.8)
        y_offset = int(((character_height / 2) - random.random() * character_height) * 0.8)

        sample = self.generate_background(128, 128)
        drawing = ImageDraw.Draw(sample)

        if random.random() > 0.9:
            draw_outlined_text(drawing, (x + x_offset, 64 + y_offset),
                               text, font=font, fill=random_color(), anchor='lm', language='ja')
        else:
            drawing.text((x + x_offset, 64 + y_offset),
                         text, font=font, fill=random_color(), anchor='lm', language='ja')

        if random.random() > 0.9:
            eat_sides(
                sample,
                64 + x_offset - character_width / 2,
                64 + x_offset + character_width / 2,
                64 + y_offset - character_height / 2,
                64 + y_offset + character_height / 2
            )

        region_score = self.generate_region_score(
            128, 128,
            top_left=(64 + x_offset - character_width / 2, 64 + y_offset - character_width / 2),
            bottom_right=(64 + x_offset + character_width / 2, 64 + y_offset + character_width / 2),
        )
        region_score = self.generate_only_char((64 + x_offset, 64 + y_offset),
                                               character, font=font, fill=random_color(), anchor='mm', language='ja')

        if self.transform is None:
            return sample, label, region_score
        else:
            return self.transform(sample), label, self.transform(region_score)

    # Characters placed randomly on the screen, underlined text
    def generate_stage_8(self):
        character_index = random.randrange(0, len(self.characters))
        character = self.characters[character_index]
        font_info = random.choice(self.fonts_supporting_glyph(character))
        font_size = self.random_font_size()
        font_size = max(8, font_size)
        font = font_info.get(font_size)

        before_count = random.randint(0, 10)
        after_count = random.randint(0, 10)
        total_count = before_count + after_count + 1
        before = [random.choice(tuple(font_info.supported_glyphs)) for _ in range(before_count)]
        after = [random.choice(tuple(font_info.supported_glyphs)) for _ in range(after_count)]
        text = ''.join(before) + character + ''.join(after)

        floating_count = int(abs(np.random.normal(0, 10)))
        floating_characters = [random.choice(tuple(font_info.supported_glyphs)) for _ in range(floating_count)]

        for extra_character in list(text) + floating_characters:
            left, top, right, bottom = font.getbbox(extra_character, anchor='lt', language='ja')
            if right == 0 or bottom == 0:
                print(f"{extra_character} is missing from {os.path.basename(font_info.path)}")
                exit(-1)

        left, top, right, bottom = font.getbbox(text, anchor='lt', language='ja')

        character_width = right / total_count
        character_height = bottom
        x = 64 - character_width / 2 - character_width * before_count
        x_offset = int(((character_width / 2) - random.random() * character_width) * 0.8)
        y_offset = int(((character_height / 2) - random.random() * character_height) * 0.8)
        x = x + x_offset
        y = 64 + y_offset

        sample = self.generate_background(128, 128)
        drawing = ImageDraw.Draw(sample)

        effect = random.choices(['outline', 'underline', 'none'], weights=[1, 1, 10])[0]
        if effect == 'outline':
            draw_outlined_text(drawing, (x, y), text, font=font, fill=random_color(), anchor='lm', language='ja')
        elif effect == 'underline':
            draw_underlined_text(drawing, (x, y), text, font=font, fill=random_color(), anchor='lm', language='ja')
        else:
            drawing.text((x, y), text, font=font, fill=random_color(), anchor='lm', language='ja')

        for floating_character in floating_characters:
            font_info = random.choice(self.fonts_supporting_glyph(floating_character))
            font_size = self.random_font_size()
            font_size = max(8, font_size)
            floating_font = font_info.get(font_size)
            f_left, f_top, f_right, f_bottom = floating_font.getbbox(floating_character, anchor='lt', language='ja')

            floating_x = []
            floating_y = []
            if y - bottom / 2 - f_bottom > -f_bottom:
                floating_y += [random.randint(-f_bottom, y - bottom // 2 - f_bottom)]
            if 128 > y + bottom / 2:
                floating_y += [random.randint(y + bottom // 2, 128)]
            if not floating_y:
                continue

            floating_x += [random.randint(-f_right, 128)]

            if random.random() > 0.9:
                draw_outlined_text(drawing, (random.choice(floating_x), random.choice(floating_y)), floating_character,
                                   font=floating_font,
                                   fill=random_color(), anchor='lt', language='ja')
            else:
                drawing.text((random.choice(floating_x), random.choice(floating_y)), floating_character, font=font,
                             fill=random_color(), anchor='lt', language='ja')

        if random.random() > 0.9:
            eat_sides(
                sample,
                64 + x_offset - character_width / 2,
                64 + x_offset + character_width / 2,
                64 + y_offset - character_height / 2,
                64 + y_offset + character_height / 2
            )

        region_score = self.generate_region_score(
            128, 128,
            top_left=(64 + x_offset - character_width / 2, 64 + y_offset - character_width / 2),
            bottom_right=(64 + x_offset + character_width / 2, 64 + y_offset + character_width / 2),
        )
        region_score = self.generate_only_char((64 + x_offset, 64 + y_offset), character, font=font, fill=random_color(), anchor='mm', language='ja')

        if self.transform is None:
            return sample, character_index, region_score
        else:
            return self.transform(sample), character_index, self.transform(region_score)

    def generate(self):
        low = math.floor(self.stage)
        high = low + 1
        if random.random() > self.stage - math.floor(self.stage):
            stage = low
        else:
            stage = high

        stage = min(stage, 8)

        if stage == 0:
            return self.generate_stage_0()
        if stage == 1:
            return self.generate_stage_1()
        if stage == 2:
            return self.generate_stage_2()
        if stage == 3:
            return self.generate_stage_3()
        if stage == 4:
            return self.generate_stage_4()
        if stage == 5:
            return self.generate_stage_5()
        if stage == 6:
            return self.generate_stage_6()
        if stage == 7:
            return self.generate_stage_7()
        if stage == 8:
            return self.generate_stage_8()

    def __iter__(self) -> Iterator[T_co]:
        while True:
            yield self.generate()

    def __getitem__(self, index) -> T_co:
        return self.generate()


if __name__ == '__main__':
    files = glob.glob(f"generated/training/*/*")
    for file in files:
        os.remove(file)


    def generate(dataset, stage, count=None):
        dataset.stage = stage
        pathlib.Path(f"generated/training/{stage}").mkdir(parents=True, exist_ok=True)
        iterator = iter(dataset)
        for i in range(len(dataset.characters) if count is None else count):
            sample, label, region_score = next(iterator)
            character = dataset.characters[label]
            sample.save(f"generated/training/{stage}/{i}_{character}_feature.png")
            region_score.save(f"generated/training/{stage}/{i}_{character}_region_score.png")


    dataset = RecognizerTrainingDataset(data_folder="data", character_set=character_sets.frequent_kanji_plus)
    generate(dataset, 0, count=50)
    generate(dataset, 1, count=50)
    generate(dataset, 2, count=50)
    generate(dataset, 3, count=50)
    generate(dataset, 4, count=50)
    generate(dataset, 5, count=50)
    generate(dataset, 6, count=50)
    generate(dataset, 7, count=50)
    generate(dataset, 8, count=50)
