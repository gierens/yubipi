#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK

import RPi.GPIO as gpio

from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter, FileType
from argcomplete import autocomplete
from evdev import InputDevice, categorize, ecodes, list_devices
from time import sleep
from threading import Thread
import inquirer
from sys import argv, stderr

from flask import Flask
from flask_restful import Resource, Api


scancodes = {
    0: None, 1: 'esc', 2: '1', 3: '2', 4: '3', 5: '4', 6: '5', 7: '6', 8: '7',
    9: '8', 10: '9', 11: '0', 12: '-', 13: '=', 14: 'bksp', 15: 'tab', 16: 'q',
    17: 'w', 18: 'e', 19: 'r', 20: 't', 21: 'y', 22: 'u', 23: 'i', 24: 'o',
    25: 'p', 26: '[', 27: ']', 28: 'crlf', 29: 'lctrl', 30: 'a', 31: 's',
    32: 'd', 33: 'f', 34: 'g', 35: 'h', 36: 'j', 37: 'k', 38: 'l', 39: ';',
    40: '"', 41: '`', 42: 'lshft', 43: '\\', 44: 'z', 45: 'x', 46: 'c',
    47: 'v', 48: 'b', 49: 'n', 50: 'm', 51: ',', 52: '.', 53: '/',
    54: 'rshft', 56: 'lalt', 100: 'ralt'
}

modhex_chars = [
    'l', 'n', 'r', 't', 'u', 'v', 'c', 'b',
    'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k',
]


def initialize_gpio():
    gpio.setmode(gpio.BOARD)


def finalize_gpio():
    gpio.cleanup()


class Yubikey():
    __input_device = None
    __gpio_pin = None
    __press_duration = None
    __release_duration = None
    __read_timeout = None
    __click_and_read_retries = None
    __last_otp = None
    __interupt_read = None

    def __init__(self, input_device, gpio_pin, press_duration=0.5,
                 release_duration=0.5, read_timeout=3,
                 click_and_read_retries=2):
        self.__input_device = InputDevice(input_device)
        self.__gpio_pin = gpio_pin
        self.__press_duration = press_duration
        self.__release_duration = release_duration
        self.__read_timeout = read_timeout
        self.__click_and_read_retries = click_and_read_retries
        self.__interupt_read = False
        gpio.setup(self.__gpio_pin, gpio.OUT, initial=gpio.LOW)
        self.__input_device.grab()

    def __del__(self):
        self.__input_device.ungrab()
        self.__input_device.close()

    def __str__(self):
        return 'Yubikey(input_device={}, gpio_pin={})'.format(
            self.__input_device.path,
            self.__gpio_pin
        )

    def press(self):
        gpio.output(self.__gpio_pin, gpio.HIGH)

    def release(self):
        gpio.output(self.__gpio_pin, gpio.LOW)

    def click(self):
        self.press()
        sleep(self.__press_duration)
        self.release()
        sleep(self.__release_duration)

    def read(self):
        otp = ''
        while not self.__interupt_read:
            done = False
            try:
                for event in self.__input_device.read():
                    if event.type != ecodes.EV_KEY:
                        continue
                    data = categorize(event)
                    if data.keystate != 1:
                        continue
                    key = scancodes.get(data.scancode, None)
                    if key == 'crlf':
                        done = True
                        break
                    elif len(key) == 1 and key in modhex_chars:
                        otp += key
                    else:
                        return None
            except BlockingIOError:
                pass
            if done:
                break
        if len(otp) == 32:
            self.__last_otp = otp
        return self.__last_otp

    def click_and_read(self):
        previous_otp = self.__last_otp
        for _ in range(self.__click_and_read_retries + 1):
            read_thread = Thread(target=self.read)
            read_thread.start()
            self.click()
            timeout = max(0, self.__read_timeout - self.__press_duration
                          - self.__release_duration)
            read_thread.join(timeout=timeout)
            self.__interupt_read = True
            while read_thread.is_alive():
                sleep(0.1)
            self.__interupt_read = False
            if self.__last_otp and self.__last_otp != previous_otp:
                return self.__last_otp
        return None


class OTP(Resource):
    def get(self):
        return {'hello': 'world'}


# TODO maybe also a function/command to just list the devices


def detect_yubikey_device_file():
    input_devices = [InputDevice(path) for path in list_devices()]
    yubikey_devices = []
    for device in input_devices:
        if device.name.startswith("Yubico YubiKey") and 'OTP' in device.name:
            yubikey_devices.append(device)
    num_yubikeys = len(yubikey_devices)
    if num_yubikeys == 1:
        return yubikey_devices[0].path
    elif num_yubikeys > 1:
        choices = [device.path for device in yubikey_devices]
        questions = [
            inquirer.List('device',
                          message='Found multiple YubiKeys. ' +
                                  'Which do you want to use?',
                          choices=choices
                          )
        ]
        answers = inquirer.prompt(questions)
        return answers['device']
    return None


def setup_parser():
    parser = ArgumentParser(
        description='''
        YubiPi is a project to take the burden of pressing a Yubikey manually
        of you, first and formost for automating things. For that the Yubikey
        is connected to a Raspberry Pi via USB and with its touch sensor
        connected to the GPIO pins over a small circuit. This program is then
        used to trigger the Yubikey and retrieve the outputted
        One-Time-Password.
        ''',
        # It can also serve in REST-API fashion, to make
        # the Yubikey available remotely.
        # ''',
        formatter_class=ArgumentDefaultsHelpFormatter,
    )

    # TODO do more thorough argument checks
    parser.add_argument('-d',
                        '--device',
                        type=FileType('r'),
                        default=None,
                        help='''Input device file of the Yubikey. If not
                        given the program tries to detect the YubiKey and
                        in case multiple are found asks what to choose.''',
                        )
    parser.add_argument('-p',
                        '--pin',
                        type=int,
                        default=40,
                        help='''Raspberry PI GPIO pin number connected to
                        the triggering circuit''',
                        )
    parser.add_argument('-t',
                        '--timeout',
                        type=float,
                        default=3,
                        help='''Timeout when trying to read from the Yubikey
                        in seconds. Note that the sum of press and release
                        duration is the lower boundary for the timeout, even
                        if you can specify a lower one.''',
                        )
    parser.add_argument('-r',
                        '--retries',
                        type=int,
                        default=2,
                        help='''Number of retries when clicking and reading
                        the Yubikey fails''',
                        )
    parser.add_argument('-P',
                        '--press-duration',
                        type=float,
                        default=0.5,
                        help='''Minimum duration between pressing and
                        releasing the Yubikey touch sensor''',
                        )
    parser.add_argument('-R',
                        '--release-duration',
                        type=float,
                        default=0.5,
                        help='''Minimum duration between releasing and
                        pressing the Yubikey touch sensor''',
                        )
    parser.add_argument('-s',
                        '--server',
                        type=bool,
                        action='store_true',
                        default=False,
                        help='''Run the program in REST API server mode. It
                        will listen for GET / with a valid authentication
                        token and return an OTP.''',
                        )
    # TODO host and port arguments

    return parser


def parse_args(parser):
    autocomplete(parser)
    args = parser.parse_args()
    return args


def main():
    parser = setup_parser()
    args = parse_args(parser)

    # TODO this should actually be part of parse_args(), but we cannot
    # easily create args.device.name if args.device is None
    device = None
    if args.device:
        device = args.device.name
    else:
        device = detect_yubikey_device_file()
    if not device:
        print(f'{argv[0]}: error: no yubikey detected or specified.',
              file=stderr)
        exit(1)

    initialize_gpio()

    yubikey = Yubikey(
        input_device=device,
        gpio_pin=args.pin,
        read_timeout=args.timeout,
        click_and_read_retries=args.retries,
        press_duration=args.press_duration,
        release_duration=args.release_duration,
    )

    if args.server:
        try:
            app = Flask(__name__)
            api = Api(app)

            api.add_resource(OTP, '/')
            app.run(debug=True)
        finally:
            finalize_gpio()
    else:
        otp = None
        try:
            otp = yubikey.click_and_read()
        finally:
            finalize_gpio()

            if otp:
                print(otp)
            else:
                exit(1)


if __name__ == '__main__':
    main()
