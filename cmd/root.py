import numpy as np
import click
import os
import cv2
import csv
import logging

from blind_watermark import WaterMark
from blind_watermark import att
from blind_watermark.recover import recover_crop

from email.mime.multipart import MIMEMultipart
from smtplib import SMTP_SSL
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import base64

# context labels
LABEL_WM_BIT_LEN = 'wm-len'

# batch input csv header
BATCH_INPUT_WATERMARK = 'watermark'
BATCH_INPUT_EMAIL = 'mail'
BATCH_INPUT_CSV_HEADER = [
    BATCH_INPUT_WATERMARK,
    BATCH_INPUT_EMAIL,
]

# decode keys, saved as csv
DECODE_KEY_PASSWORD = 'password'
DECODE_KEY_WATERMARK_BIT_LEN = 'wm_bit_len'
DECODE_KEY_BLOCK_SIZE = 'block_size'
DECODE_KEY_RESIST = 'resist'
BATCH_SRC_FILE = 'src.png'
BATCH_SAVE_FILE = 'save.csv'
BATCH_SAVE_FILE_HEADERS = [
    DECODE_KEY_PASSWORD,
    DECODE_KEY_WATERMARK_BIT_LEN,
    DECODE_KEY_BLOCK_SIZE,
    DECODE_KEY_RESIST,
]


def encode_wm(wm, wm_bit_len=0):
    byte = bin(int(wm.encode('utf-8').hex(), base=16))[2:]
    wm_bit = np.array(np.array(list(byte)) == '1')
    while len(wm_bit) < wm_bit_len:
        wm_bit = np.insert(wm_bit, 0, False)

    return wm_bit


def decode_wm(wm_bit):
    binary_str = ''.join(['1' if bit else '0' for bit in wm_bit])
    integer = int(binary_str, base=2)
    hex_str = hex(integer)[2:]
    byte_seq = bytes.fromhex(hex_str)
    return byte_seq.decode('utf-8')


def encode_image(src=None, target=None, pwd=None, wm=None, block_size=16, resist=25):
    bwm = WaterMark(password_img=pwd, password_wm=pwd, resist=resist, block_size=block_size)
    bwm.read_img(src)
    bwm.read_wm(wm, mode='bit')
    bwm.embed(target)


def decode_image(src=None, pwd=None, wm_bit_len=None, block_size=16, resist=25):
    bwm = WaterMark(password_img=pwd, password_wm=pwd, resist=resist, block_size=block_size)
    wm_bit = bwm.extract(filename=src, wm_shape=wm_bit_len, mode='bit')
    wm = decode_wm(wm_bit)
    return wm


def preview_image(image, password, wm, verify, expected):
    preview_dir = '_preview'
    block_size_list = [4, 8, 16]
    resist_list = [20, 25, 30, 35, 40]
    os.makedirs(preview_dir, 0o777, exist_ok=True)
    basename = os.path.basename(image)
    for block_size in block_size_list:
        for resist in resist_list:
            target = os.path.join(preview_dir, str(block_size)+'-'+str(resist)+'-'+basename)
            encode_image(image, target, password, wm, block_size, resist)
            if verify:
                ok = verify_image(target, expected, password, wm.size, block_size, resist)
                if ok:
                    logging.info('PASS: ', target)
                else:
                    logging.warning('FAIL: ', target)


def verify_image(image, expected, pwd, wm_bit_len, block_size, resist):
    verify_dir = '_verify'
    os.makedirs(verify_dir, 0o777, exist_ok=True)

    testcases = [
        os.path.join(verify_dir, 'crop.png'),
        os.path.join(verify_dir, 'crop_scale_recover.png'),
        os.path.join(verify_dir, 'salt_pepper.png'),
        os.path.join(verify_dir, 'rotate_recover.png'),
        os.path.join(verify_dir, 'cover.png'),
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
    recover_crop(template_file=tmp_file, output_file_name=testcases[1], loc=(x1, y1, x2, y2), image_o_shape=ori_img_shape)

    # testcase 2
    att.salt_pepper_att(input_filename=image, output_file_name=testcases[2], ratio=0.05)

    # testcase 3
    tmp_file = os.path.join(verify_dir, 'rotate.png')
    att.rot_att(input_filename=image, output_file_name=tmp_file, angle=45)
    att.rot_att(input_filename=tmp_file, output_file_name=testcases[3], angle=-45)

    # testcase 4
    att.shelter_att(input_filename=image, output_file_name=testcases[4], ratio=0.1, n=60)

    # testcase 5
    tmp_file = os.path.join(verify_dir, 'resize.png')
    att.resize_att(input_filename=image, output_file_name=tmp_file, out_shape=(800, 600))
    att.resize_att(input_filename=tmp_file, output_file_name=testcases[5], out_shape=ori_img_shape[::-1])

    for testcase in testcases:
        try:
            wm = decode_image(testcase, pwd, wm_bit_len, block_size, resist)
        except ValueError as e:
            logging.error(f'failed to decode watermark: {e}')
            return False

        if wm != expected:
            logging.error(f'verify failed, testcase: {testcase:s}, expected {expected:s}, decode {wm:s}')
            return False
    return True


@click.group()
@click.option('--wm-bit-len', default=128, show_default=True, help='For watermarks shorter than `--wm-len`, add bits at the beginning.')
@click.pass_context
def root(ctx, wm_bit_len):
    ctx.ensure_object(dict)
    ctx.obj[LABEL_WM_BIT_LEN] = wm_bit_len
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')


@root.command()
@click.argument('image', required=True, type=click.Path(exists=True))
@click.argument('wm', required=True)
@click.option('-p', '--password', show_default=True, default=1)
@click.option('-o', '--output', show_default=True, default='output.png')
@click.option('--block-size', show_default=True, default=4)
@click.option('--resist', show_default=True, default=35)
@click.option('--verify', show_default=True, default=False, is_flag=True, help='try to decoded image')
@click.option('--preview', show_default=True, default=False, is_flag=True, help='show encoded images with a group of block-size/resist')
@click.pass_context
def encode(ctx, image, wm, password, output, block_size, resist, verify, preview):
    wm_bit_len = ctx.obj[LABEL_WM_BIT_LEN]
    wm_bit = encode_wm(wm, wm_bit_len)
    logging.info(f'watermark (string): {wm}')
    wm_bit_show = ''.join(['1' if bit else '0' for bit in wm_bit])
    logging.info(f'watermark (binary): {wm_bit_show}')
    if wm_bit.size > wm_bit_len:
        logging.warning(f'watermark bits length {wm_bit.size:d} exceed wm_bit_len {wm_bit_len:d}')

    if preview:
        preview_image(image, password, wm_bit, verify, wm)
        return

    encode_image(image, output, int(password), wm_bit, block_size, resist)
    if verify:
        ok = verify_image(output, wm, password, wm_bit.size, block_size, resist)
        if not ok:
            logging.error('image verify failed, please try other arguments')
            return

    logging.info(f'success encode from {image:s} to {output:s}')
    logging.info(f'please store the following parameters to decode image:')
    logging.info(f'password:   {password}')
    logging.info(f'wm_bit_len:   {wm_bit.size}')
    logging.info(f'block_size: {block_size}')
    logging.info(f'resist:     {resist}')


@root.command()
@click.argument('image', required=True, type=click.Path(exists=True))
@click.option('-p', '--password', show_default=True, default=1)
@click.option('--block-size', show_default=True, default=4)
@click.option('--resist', show_default=True, default=35)
@click.pass_context
def decode(ctx, image, password, block_size, resist):
    wm_bit_len = ctx.obj[LABEL_WM_BIT_LEN]
    wm = decode_image(image, password, wm_bit_len, block_size, resist)
    logging.info(f'success decode from {image}: {wm}')


def is_batch_image_dir(item):
    return str(item).isdigit() and len(str(item)) == 2


def batch_sort(batch_dir):
    items = [-1]
    for item in os.listdir(batch_dir):
        if is_batch_image_dir(item):
            items.append(int(item))
    items.sort()
    for i in range(1, len(items), 1):
        if items[i] - items[i - 1] != 1:
            old = os.path.join(batch_dir, '{:02d}'.format(items[i]))
            new = os.path.join(batch_dir, '{:02d}'.format(items[i - 1] + 1))
            os.rename(old, new)
            items[i] = items[i - 1] + 1
    return items[1:]


def batch_clean_up(batch_dir, image_id):
    image_dir = os.path.join(batch_dir, '{:02d}'.format(image_id))
    for item in os.listdir(image_dir):
        if item != BATCH_SRC_FILE:
            os.remove(os.path.join(image_dir, item))


def batch_read_csv(filename):
    data = []
    with open(filename, mode='r', encoding='utf-8') as file:
        reader = csv.DictReader(file, fieldnames=BATCH_SAVE_FILE_HEADERS)
        next(reader)
        for row in reader:
            line = [
                row[DECODE_KEY_PASSWORD],
                row[DECODE_KEY_WATERMARK_BIT_LEN],
                row[DECODE_KEY_BLOCK_SIZE],
                row[DECODE_KEY_RESIST],
            ]
            data.append(line)
    return data


def batch_write_csv(filename, data):
    with open(filename, mode='w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(BATCH_SAVE_FILE_HEADERS)
        writer.writerows(data)


def batch_reduce(batch_dir):
    final_target = os.path.join(batch_dir, BATCH_SAVE_FILE)
    try:
        os.remove(final_target)
    except FileNotFoundError:
        pass

    data = []
    for item in os.listdir(batch_dir):
        if not is_batch_image_dir(item):
            continue
        target = os.path.join(batch_dir, item, BATCH_SAVE_FILE)
        line = batch_read_csv(target)
        data.append(line[0])

    batch_write_csv(final_target, data)


'''
In batch, all encoded watermark could not be longer than wm_bit_len
'''
@root.command()
@click.argument('batch-dir', required=True)
@click.argument('csv-file', required=True, type=click.Path(exists=True))
@click.option('-i', '--image-id', help='only encode specific image, note batch command will sort IDs ahead')
@click.option('-f', '--force', show_default=True, default=False, is_flag=True, help='force re-encode')
@click.option('-p', '--password', show_default=True, default=1)
@click.option('--block-size', show_default=True, default=4)
@click.option('--resist', show_default=True, default=35)
@click.option('--verify', show_default=True, default=False, is_flag=True, help='try to decoded image')
@click.pass_context
def batch_encode(ctx, batch_dir, csv_file, image_id, force, password, block_size, resist, verify):
    os.makedirs(batch_dir, 0o777, exist_ok=True)

    # serialisation images
    items = batch_sort(batch_dir)

    images = []
    if image_id is not None:
        images.append(int(image_id))
    else:
        images = items

    for image_id in images:
        if force:
            batch_clean_up(batch_dir, image_id)

        image_dir = os.path.join(batch_dir, '{:02d}'.format(image_id))
        image_file = os.path.join(image_dir, BATCH_SRC_FILE)
        os.makedirs(image_dir, 0o777, exist_ok=True)

        with open(csv_file, mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file, fieldnames=[BATCH_INPUT_WATERMARK])
            next(reader)

            for row in reader:
                wm = row[BATCH_INPUT_WATERMARK]
                target = os.path.join(image_dir, wm + '.png')

                if os.path.exists(target) and not force:
                    logging.info(f'skip: {target}')
                    continue

                if os.path.exists(target):
                    logging.info(f'file {target} exists, skip encode')
                    continue

                wm_bit_len = ctx.obj[LABEL_WM_BIT_LEN]
                wm_bit = encode_wm(wm, wm_bit_len)
                if wm_bit.size > wm_bit_len:
                    logging.error(f'watermark {wm} has encoded length {wm_bit.size} exceed wm_bit_len {wm_bit_len}, '
                                  f'please try other encode arguments and re-do this batch')
                    return

                encode_image(image_file, target, int(password), wm_bit, block_size, resist)
                if verify:
                    ok = verify_image(target, wm, password, wm_bit.size, block_size, resist)
                    if not ok:
                        logging.error(f'watermark {wm} cannot pass verification, '
                                      f'please try other encode arguments and re-do this batch')
                        return
                logging.info(f'success: {target}')

        save_file = os.path.join(image_dir, 'save.csv')
        if os.path.exists(save_file) and not force:
            logging.info(f'skip rewrite save_file {save_file}')
            continue
        with open(save_file, mode='w', encoding='utf-8', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(['password', 'wm_bit_len', 'block_size', 'resist'])
            writer.writerow([password, ctx.obj[LABEL_WM_BIT_LEN], block_size, resist])

    batch_reduce(batch_dir)


def order_msg(batch_dir, user, to_watermark, to_email):
    msg = MIMEMultipart()
    from_name = base64.b64encode('客服小祥为您服务'.encode('utf-8')).decode('utf-8')
    from_name = '=?utf-8?B?' + from_name + '?=' + ' <' + user + '>'

    msg['Subject'] = '亲，画集已发货'
    msg['From'] = from_name
    msg['To'] = to_email

    # text part
    msg_image = MIMEText('我是来结束这个订单的', _charset='utf-8')
    msg.attach(msg_image)

    # image part
    for image_id in os.listdir(batch_dir):
        if not is_batch_image_dir(image_id):
            continue
        target = os.path.join(batch_dir, image_id, to_watermark + '.png')
        if not os.path.exists(target):
            logging.warning(f'user {to_email} don\'t found image {target}')
            continue
        with open(target, 'rb') as file:
            msg_image = MIMEImage(file.read())
            filename = str(image_id) + '.png'
            msg_image.add_header('Content-Disposition', 'attachment', filename=filename)
            msg.attach(msg_image)
    return msg


# send overview to self
def overview_msg(batch_dir, user):
    msg = MIMEMultipart()
    from_name = base64.b64encode('客服小祥为您服务'.encode('utf-8')).decode('utf-8')
    from_name = '=?utf-8?B?' + from_name + '?=' + ' <' + user + '>'

    msg['Subject'] = '画集统计'
    msg['From'] = from_name
    msg['To'] = user

    content = '''
    <html>
    <head>
    <style>
        th, td {
            border: 1px solid black;
            padding: 8px;
            text-align: left;
        }
        .image-container {
            text-align: center;
            margin: 10px 0;
        }
        .image-caption {
            font-size: smaller;
            margin-top: 5px;
            text-align: center;
        }
    </style>
    </head>
    <body>
    <p>盲水印解密key如下:</p>
    <table border='1' width:100% border-collapse:collapse>
    '''

    # decode key table
    headers = ['image_id']
    for header in BATCH_SAVE_FILE_HEADERS:
        headers.append(header)
    data = batch_read_csv(os.path.join(batch_dir, BATCH_SAVE_FILE))
    content += '<tr>' + ''.join([f'<th>{header}</th>' for header in headers]) + '</tr>'
    for idx, row in enumerate(data):
        line = ['{:02d}'.format(idx)]
        for cell in row:
            line.append(cell)
        content += '<tr>' + ''.join([f'<td>{cell}</td>' for cell in line]) + '</tr>'
    content += '</table>'

    # image list
    content += '<p>对应图片:</p>'
    for image_id in os.listdir(batch_dir):
        if not is_batch_image_dir(image_id):
            continue
        target = os.path.join(batch_dir, image_id, BATCH_SRC_FILE)
        with open(target, 'rb') as file:
            img = MIMEImage(file.read())
            img.add_header('Content-ID', image_id)
            content += '<table width:auto border-collapse:collapse><tr>'
            content += f'<td><img src="cid:{image_id}" alt="{image_id}" style="display: block; max-width: 200px; height: auto;></td>'
            content += f'<td style="padding-left: 10px;"><div class="image-caption">{image_id}</div></td>'
            content += '</tr></table>'
            msg.attach(img)

    content += '</body>'
    content += '</html>'

    msg_text = MIMEText(content, _subtype = 'html', _charset = 'utf-8')
    msg.attach(msg_text)

    return msg


@root.command()
@click.argument('batch-dir', required=True, type=click.Path(exists=True))
@click.argument('csv-file', required=True, type=click.Path(exists=True))
@click.option('-u', '--user', required=True, help='user of sender email')
@click.option('-t', '--token', required=True, help='token of sender email')
@click.pass_context
def email(ctx, batch_dir, csv_file, user, token):
    with open(csv_file, mode='r', encoding='utf-8') as file:
        reader = csv.DictReader(file, fieldnames=BATCH_INPUT_CSV_HEADER)
        next(reader)

        # send order msg
        for row in reader:
            wm = row[BATCH_INPUT_WATERMARK]
            email = row[BATCH_INPUT_EMAIL]
            msg = order_msg(batch_dir, user, wm, email)
            with SMTP_SSL(host='smtp.qq.com', port=465) as smtp:
                smtp.login(user=user, password=token)
                smtp.sendmail(from_addr=user, to_addrs=[email, user], msg=msg.as_string())
                logging.info(f'success to send order email to {email}, watermark: {wm}')

    # send overview msg
    msg = overview_msg(batch_dir, user)
    with SMTP_SSL(host='smtp.qq.com', port=465) as smtp:
        smtp.login(user=user, password=token)
        smtp.sendmail(from_addr=user, to_addrs=[user], msg=msg.as_string())
    logging.info(f'success to send overview email to self {user}')


if __name__ == '__main__':
    root()
