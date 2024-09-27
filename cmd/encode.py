import csv
import os
from prettytable import PrettyTable
import logging
import threading
from helpers import Helpers
import blind_watermark

from email.mime.multipart import MIMEMultipart
from smtplib import SMTP_SSL
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.base import MIMEBase
import base64
import tarfile
from email import encoders

EMAIL_SENDER_USER = ''
EMAIL_SENDER_TOKEN = ''
EMAIL_SMTP_SERVER = ''

DEFAULT_REPORT_EMAIL = ''

class Colors:
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    RESET = '\033[0m'

class Encoder:
    def __init__(self, workdir = '_encode', wm_bit_len = 128):
        self.workdir = workdir
        self.wm_bit_len = wm_bit_len
        self.preview_sem = threading.Semaphore(6)

        # inline data
        self.src_images = []
        self.password = 1
        self.watermarks = []
        self.emails = []
        self.block_sizes = []
        self.resists = []
        self.encoded = []


    def run(self):
        os.makedirs(self.workdir, 0o777, exist_ok=False)
        os.makedirs(self.__src_dir_path(), 0o777)
        os.makedirs(self.__target_dir_path(), 0o777)
        os.makedirs(self.__archive_path(), 0o777)

        src_images = []
        print("请按顺序输入原图路径，输入0表示所有原图输入完成，进入下一步")
        while True:
            user_input = input('图片' + '{:02d}'.format(len(src_images)) + ':')
            if user_input == '0':
                print("所有原图输入完成，准备编码...")
                break
            elif not os.path.exists(user_input):
                print(f'图片 {user_input} 不存在，请重新输入')
            else:
                src = user_input
                target = self.__src_image_path(len(src_images))
                src_images.append(target)
                with open(src, 'rb') as f_src:
                    content = f_src.read()
                with open(target, 'wb') as f_target:
                    f_target.write(content)
        self.src_images = src_images
        self.block_sizes = [4] * len(src_images)
        self.resists = [35] * len(src_images)
        self.encoded = [{} for _ in range(len(self.src_images))]

        while True:
            user_input = input('''
输入命令对应的序号，比如输入1表示查看当前状态
1. 查看当前状态
2. 修改密码（默认密码为1）
3. 添加水印（添加用户）
4. 从csv批量添加用户
5. 生成图片质量预览
6. 调整图片质量参数
7. 对所有图片执行编码，可重复执行
8. 打包并发送邮件，执行此命令后退出

输入你的命令:''')
            if user_input == '1':
                self.__cmd_status()
            elif user_input == '2':
                self.__cmd_update_password()
            elif user_input == '3':
                self.__cmd_add_user()
            elif user_input == '4':
                self.__cmd_add_user_from_csv()
            elif user_input == '5':
                self.__cmd_preview()
            elif user_input == '6':
                self.__cmd_update_image_args()
            elif user_input == '7':
                self.__cmd_encode_all_images()
            elif user_input == '8':
                self.__cmd_send_email()
                break


    def __cmd_status(self):
        print(f'\npassword: {self.password}')

        headers = ['image', 'block_size', 'resist']
        email_row = ['', '', '']
        for i, wm in enumerate(self.watermarks):
            headers.append(wm)
            if self.emails[i] != '':
                email_row.append(self.emails[i])
            else:
                email_row.append(f'{Colors.YELLOW}无邮箱{Colors.RESET}')

        data = []
        for i, image in enumerate(self.src_images):
            row = [os.path.basename(image), self.block_sizes[i], self.resists[i]]
            for watermark in self.watermarks:
                if self.__is_encoded(i, watermark):
                    row.append(f'{Colors.GREEN}√{Colors.RESET}')
                else:
                    row.append('')
            data.append(row)

        table = PrettyTable()
        table.field_names = headers
        table.add_row(email_row)
        table.add_rows(data)
        print(table)


    def __cmd_update_password(self):
        print(f'旧密码: {self.password}')
        while True:
            password = input('请输入新密码:')
            if self.__is_password_valid(password):
                break
            else:
                print('密码应只包含大小写字母和数字, 请重新输入')
        self.password = password
        for i, image in enumerate(self.src_images):
            self.__reset_image_encode_status(i)
        print(f'新密码: {self.password}')


    def __cmd_add_user(self):
        watermark = input('请输入水印:')
        email = input('请输入邮箱，没有的话直接回车:')
        if not self.__add_user(watermark, email):
            logging.warning('添加用户失败')


    def __cmd_add_user_from_csv(self):
        HEADER_WATERMARK = 'watermark'
        HEADER_EMAIL = 'email'

        print('注意必须使用csv文件，excel需导出成csv才能用')
        print(f'请保证csv文件中至少包含两列，列头名称分别为 {HEADER_WATERMARK} 和 {HEADER_EMAIL}，其中{HEADER_EMAIL}列可以有空行')
        csv_file_path = input('请输入csv文件路径:')
        with open(csv_file_path, mode='r', encoding='utf-8') as file:
            reader = csv.DictReader(file, fieldnames=[HEADER_WATERMARK, HEADER_EMAIL])
            next(reader)

            for row in reader:
                wm = row[HEADER_WATERMARK]
                email = row[HEADER_EMAIL]
                if not self.__add_user(wm, email):
                    logging.warning(f'添加用户失败, watermark: {wm}, email: {email}')


    def __add_user(self, watermark, email):
        wm_bit = Helpers.encode_wm(watermark, self.wm_bit_len)
        if len(wm_bit) > self.wm_bit_len:
            print(f'水印 {watermark} 编码后长度超限 {len(wm_bit)} > {self.wm_bit_len}')
            return False
        if len(email) > 0 and not '@' in email:
            logging.warning(f'email {email} 不合规，忽略处理')
            email = ''

        os.makedirs(self.__target_watermark_dir_path(watermark), 0o777)
        self.watermarks.append(watermark)
        self.emails.append(email)
        return True


    def __cmd_preview(self):
        print('正在生成预览图片，请稍等...')
        block_size_list = [4, 8, 16]
        resist_list = [25, 30, 35]

        verify_results = [list] * len(self.src_images)
        def preview_image_thread(image_id):
            verify_results[image_id] = self.__preview_image(image_id, block_size_list, resist_list)

        preview_threads = []
        for i, image in enumerate(self.src_images):
            thread = threading.Thread(target=preview_image_thread, args=(i,))
            thread.start()
            preview_threads.append(thread)

        for thread in preview_threads:
            thread.join()

        headers = ['image']
        for block_size in block_size_list:
            for resist in resist_list:
                headers.append('{:d}-{:d}'.format(block_size, resist))

        data = []
        for i, image in enumerate(self.src_images):
            row = ['{:02d}'.format(i)]
            results = verify_results[i]
            for idx in range(0, len(block_size_list) * len(resist_list), 1):
                if results[idx]:
                    row.append(f'{Colors.GREEN}√{Colors.RESET}')
                else:
                    row.append(f'{Colors.RED}x{Colors.RESET}')
            data.append(row)

        table = PrettyTable()
        table.field_names = headers
        table.add_rows(data)
        print(table)

    def __cmd_update_image_args(self):
        image_id = int(input('输入调整图片的序号，或者输入-1返回:'))
        block_size = input('block_size:')
        resist = input('resist:')
        if image_id < 0 or image_id > len(self.src_images):
            return
        self.block_sizes[image_id] = block_size
        self.resists[image_id] = resist
        self.__reset_image_encode_status(image_id)


    def __cmd_encode_all_images(self):
        encode_sem = threading.Semaphore(6)
        def encode_thread(image_id, watermark):
            with encode_sem:
                target = self.__target_image_path(image_id, watermark)
                wm_bit = self.__encode_wm(watermark)
                b = int(self.block_sizes[image_id])
                r = int(self.resists[image_id])

                logging.info(f'generating {target} ...')
                Helpers.encode_image(self.src_images[image_id], target, self.password, wm_bit, b, r)
                logging.info(f'generated {target}')

                verify_dir = os.path.join(self.__target_watermark_dir_path(watermark), 'verify', str(b) + '-' + str(r))
                ok = Helpers.verify_image(target, watermark, self.password, self.wm_bit_len, b, r, verify_dir)
                if ok:
                    self.encoded[image_id][watermark] = True
                else:
                    logging.warning(f'target verify FAIL: {target}, have to change block_size or resist')

        threads = []
        for i, image in enumerate(self.src_images):
            for wm in self.watermarks:
                if self.__is_encoded(i, wm):
                    logging.info(f'SKIP {self.__target_image_path(i, wm)}')
                else:
                    thread = threading.Thread(target=encode_thread, args=(i, wm))
                    thread.start()
                    threads.append(thread)

        for thread in threads:
            thread.join()


    def __cmd_send_email(self):
        email_for_report = input(f'默认将汇总报告发送至邮箱{DEFAULT_REPORT_EMAIL}，需要发送至其他邮箱请输入，使用默认就直接回车:')
        tag = input('需要的话请输入标签，将添加在邮件标题中，方便查找邮件，不需要就直接回车:')

        if email_for_report == '':
            email_for_report = DEFAULT_REPORT_EMAIL

        with SMTP_SSL(host=EMAIL_SMTP_SERVER, port=465) as smtp:
            smtp.login(user=EMAIL_SENDER_USER, password=EMAIL_SENDER_TOKEN)

            # send email (report)
            msg = self.__email_msg_report(email_for_report, tag)
            smtp.sendmail(from_addr=EMAIL_SENDER_USER, to_addrs=[email_for_report], msg=msg.as_string())
            print(f'汇总报告已发送至 {email_for_report}')

            # send email (archive)
            for i, wm in enumerate(self.watermarks):
                self.__archive(wm)
                email = self.emails[i]
                if email == '':
                    print(f'水印 {Colors.YELLOW}{wm}{Colors.RESET} 没有邮箱可以发送，压缩包在 {self.__archive_watermark_path(wm)}')
                    continue
                msg = self.__email_msg_archive(email, self.__archive_watermark_path(wm))
                smtp.sendmail(from_addr=EMAIL_SENDER_USER, to_addrs=[email], msg=msg.as_string())
                print(f'水印 {Colors.GREEN}{wm}{Colors.RESET} 已发送至邮箱 {email}')


    def __email_msg_archive(self, receiver, filename):
        msg = MIMEMultipart()
        from_name = base64.b64encode('客服小祥为您服务'.encode('utf-8')).decode('utf-8')
        from_name = '=?utf-8?B?' + from_name + '?=' + ' <' + EMAIL_SENDER_USER + '>'
        msg['Subject'] = '亲，画集已发货'
        msg['From'] = from_name
        msg['To'] = receiver
        msg.attach(MIMEText('见附件', _charset='utf-8'))
        with open(filename, 'rb') as file:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(file.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename=images.tgz')
            msg.attach(part)
        return msg


    def __email_msg_report(self, receiver, tag):
        msg = MIMEMultipart()
        from_name = base64.b64encode('客服小祥为您服务'.encode('utf-8')).decode('utf-8')
        from_name = '=?utf-8?B?' + from_name + '?=' + ' <' + EMAIL_SENDER_USER + '>'

        subject = f'画集报告-共{len(self.src_images)}张'
        if tag != '':
            subject += '-' + tag
        msg['Subject'] = subject
        msg['From'] = from_name
        msg['To'] = receiver

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
        headers = ['image_id', 'block_size', 'resist', 'wm_bit_len', 'password']
        content += '<tr>' + ''.join([f'<th>{header}</th>' for header in headers]) + '</tr>'
        for idx, image in enumerate(self.src_images):
            line = ['{:02d}'.format(idx), self.block_sizes[idx], self.resists[idx], self.wm_bit_len, self.password]
            content += '<tr>' + ''.join([f'<td>{cell}</td>' for cell in line]) + '</tr>'
        content += '</table>'

        # watermark list
        content += '<p>本次发货对象:</p>'
        content += "<table border='1' width:100% border-collapse:collapse>"
        content += '<tr><th> watermark </th></tr>'
        for wm in self.watermarks:
            content += f'<tr><td>{wm}</td></tr>'
        content += '</table>'

        # image list
        content += '<p>对应图片:</p>'
        for i, image in enumerate(self.src_images):
            with open(image, 'rb') as file:
                img = MIMEImage(file.read())
                image_id = '{:02d}'.format(i)
                img.add_header('Content-ID', image_id)
                content += '<table width:auto border-collapse:collapse><tr>'
                content += f'<td><img src="cid:{image_id}" alt="{image_id}" style="display: block; max-width: 200px; height: auto;></td>'
                content += f'<td style="padding-left: 10px;"><div class="image-caption">{image_id}</div></td>'
                content += '</tr></table>'
                msg.attach(img)

        content += '</body>'
        content += '</html>'

        msg_text = MIMEText(content, _subtype='html', _charset='utf-8')
        msg.attach(msg_text)
        return msg


    def __encode_wm(self, watermark):
        return Helpers.encode_wm(watermark, self.wm_bit_len)


    def __archive(self, watermark):
        target = self.__archive_watermark_path(watermark)
        if os.path.exists(target):
            logging.info(f'SKIP archive {target}')
        files = []
        for i, img in enumerate(self.src_images):
            files.append(self.__target_image_path(i, watermark))
        with tarfile.open(target, "w:gz") as tar:
            for file in files:
                tar.add(file, arcname=os.path.basename(file))



    def __preview_image(self, i, block_size_list, resist_list):
        image = self.src_images[i]
        preview_dir = self.__preview_image_dir_path(i)

        os.makedirs(preview_dir, 0o777, exist_ok=True)

        target_list = [''] * len(block_size_list) * len(resist_list)
        result_list = [0] * len(block_size_list) * len(resist_list)

        wm = '180831502'
        wm_bit = Helpers.encode_wm(wm, self.wm_bit_len)
        def preview_func(b, r, idx):
            with self.preview_sem:
                target = os.path.join(preview_dir, '{:d}-{:d}.png'.format(b, r))
                target_list[idx] = target
                logging.info(f'generating {target}...')
                Helpers.encode_image(image, target, self.password, wm_bit, b, r)
                logging.info(f'generated {target}')

                verify_dir = os.path.join(preview_dir, 'verify', str(b) + '-' + str(r))
                result_list[idx] = Helpers.verify_image(target, wm, self.password, self.wm_bit_len, b, r, verify_dir)

        block_size_args = []
        resist_args = []
        for block_size in block_size_list:
            for resist in resist_list:
                block_size_args.append(block_size)
                resist_args.append(resist)

        threads = []
        for i, block_size in enumerate(block_size_args):
            thread = threading.Thread(target=preview_func, args=(block_size_args[i], resist_args[i], i))
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

        return result_list


    @staticmethod
    def __is_password_valid(password):
        return str(password).isalnum()

    def __is_encoded(self, i, watermark):
        return self.encoded[i].get(watermark, False) and os.path.exists(self.__target_image_path(i, watermark))

    def __reset_image_encode_status(self, i):
        for watermark in self.watermarks:
            self.encoded[i].update({watermark: False})

    def __src_image_path(self, i):
        return os.path.join(self.__src_dir_path(), '{:02d}.png'.format(i))

    def __src_dir_path(self):
        return os.path.join(self.workdir, 'src')

    def __target_dir_path(self):
        return os.path.join(self.workdir, 'target')

    def __preview_dir_path(self):
        return os.path.join(self.workdir, 'preview')

    def __preview_image_dir_path(self, i):
        return os.path.join(self.__preview_dir_path(), '{:02d}'.format(i))

    def __target_watermark_dir_path(self, watermark):
        return os.path.join(self.__target_dir_path(), watermark)

    def __target_image_path(self, i, watermark):
        return os.path.join(self.__target_watermark_dir_path(watermark), '{:02d}.png'.format(i))

    def __archive_path(self):
        return os.path.join(self.workdir, 'archive')

    def __archive_watermark_path(self, watermark):
        return os.path.join(self.__archive_path(), watermark+'.tgz')

if __name__ == '__main__':
    blind_watermark.bw_notes.close()
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - %(levelname)s - %(message)s')
    Encoder().run()
