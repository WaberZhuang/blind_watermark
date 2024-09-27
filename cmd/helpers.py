import time
from threading import ThreadError

import numpy as np
import os
import cv2
import logging

from blind_watermark import WaterMark
from blind_watermark import att
from blind_watermark.recover import recover_crop
import threading

class Helpers:
    @staticmethod
    def encode_wm(wm, wm_bit_len=0):
        byte = bin(int(wm.encode('utf-8').hex(), base=16))[2:]
        wm_bit = np.array(np.array(list(byte)) == '1')
        while len(wm_bit) < wm_bit_len:
            wm_bit = np.insert(wm_bit, 0, False)

        return wm_bit

    @staticmethod
    def decode_wm(wm_bit):
        binary_str = ''.join(['1' if bit else '0' for bit in wm_bit])
        integer = int(binary_str, base=2)
        hex_str = hex(integer)[2:]
        byte_seq = bytes.fromhex(hex_str)
        return byte_seq.decode('utf-8')

    @staticmethod
    def encode_image(src=None, target=None, pwd=None, wm=None, block_size=16, resist=25):
        bwm = WaterMark(password_img=int(pwd), password_wm=int(pwd), resist=resist, block_size=block_size)
        bwm.read_img(src)
        bwm.read_wm(wm, mode='bit')
        bwm.embed(target)

    @staticmethod
    def decode_image(src=None, pwd=None, wm_bit_len=None, block_size=16, resist=25):
        bwm = WaterMark(password_img=int(pwd), password_wm=int(pwd), resist=resist, block_size=block_size)
        wm_bit = bwm.extract(filename=src, wm_shape=wm_bit_len, mode='bit')
        wm = Helpers.decode_wm(wm_bit)
        return wm

    @staticmethod
    def verify_image(image, expected, pwd, wm_bit_len, block_size, resist, verify_dir='_verify'):
        os.makedirs(verify_dir, 0o777, exist_ok=True)

        testcases = [
            os.path.join(verify_dir, 'crop.png'),
            os.path.join(verify_dir, 'crop_scale_recover.png'),
            os.path.join(verify_dir, 'salt_pepper.png'),
            # os.path.join(verify_dir, 'rotate_recover.png'),
            # os.path.join(verify_dir, 'cover.png'),
            os.path.join(verify_dir, 'resize_recover.png'),
        ]

        # testcase 0
        att.cut_att(input_filename=image, output_file_name=testcases[0], loc=((0.3, 0.1), (0.7, 0.9)))

        # testcase 1
        ori_img_shape = cv2.imread(image).shape[:2]
        h, w = ori_img_shape
        loc_r = ((0.1, 0.1), (0.5, 0.5))
        scale = 0.7
        x1, y1, x2, y2 = int(w * loc_r[0][0]), int(h * loc_r[0][1]), int(w * loc_r[1][0]), int(h * loc_r[1][1])
        tmp_file = os.path.join(verify_dir, 'crop_scale.png')
        att.cut_att3(input_filename=image, output_file_name=tmp_file, loc=(x1, y1, x2, y2), scale=scale)
        recover_crop(template_file=tmp_file, output_file_name=testcases[1], loc=(x1, y1, x2, y2),
                     image_o_shape=ori_img_shape)

        # testcase 2
        att.salt_pepper_att(input_filename=image, output_file_name=testcases[2], ratio=0.05)

        # testcase 3
        # tmp_file = os.path.join(verify_dir, 'rotate.png')
        # att.rot_att(input_filename=image, output_file_name=tmp_file, angle=45)
        # att.rot_att(input_filename=tmp_file, output_file_name=testcases[3], angle=-45)

        # testcase 4
        # att.shelter_att(input_filename=image, output_file_name=testcases[4], ratio=0.1, n=60)

        # testcase 5
        tmp_file = os.path.join(verify_dir, 'resize.png')
        att.resize_att(input_filename=image, output_file_name=tmp_file, out_shape=(800, 600))
        att.resize_att(input_filename=tmp_file, output_file_name=testcases[3], out_shape=ori_img_shape[::-1])

        for testcase in testcases:
            try:
                wm = Helpers.decode_image(testcase, pwd, wm_bit_len, block_size, resist)
            except ValueError as e:
                logging.error(f'verify FAIL, testcase: {testcase:s}, failed to decode watermark: {e}')
                return False

            if wm != expected:
                logging.error(f'verify FAIL, testcase: {testcase:s}, expected {expected:s}, decode {wm:s}')
                return False
            logging.info(f'testcase ok: {testcase:s}')
        logging.info(f'verify PASS, image: {image:s}')
        return True