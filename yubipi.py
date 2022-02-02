#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK

import RPi.GPIO as gpio

from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter, FileType
from argcomplete import autocomplete
from evdev import InputDevice, categorize, ecodes, list_devices
from time import sleep
from threading import Thread, Semaphore
import inquirer
from sys import argv, stderr
from functools import wraps
from http import HTTPStatus
from secrets import token_hex
import logging

from flask import Flask, request, jsonify, make_response
from flask_restful import Resource, Api
from waitress import serve


LOG_LEVELS = {
    0: logging.ERROR,
    1: logging.WARN,
    2: logging.INFO,
    3: logging.DEBUG,
}


app = None


SCANCODES = {
    0: None, 1: 'esc', 2: '1', 3: '2', 4: '3', 5: '4', 6: '5', 7: '6', 8: '7',
    9: '8', 10: '9', 11: '0', 12: '-', 13: '=', 14: 'bksp', 15: 'tab', 16: 'q',
    17: 'w', 18: 'e', 19: 'r', 20: 't', 21: 'y', 22: 'u', 23: 'i', 24: 'o',
    25: 'p', 26: '[', 27: ']', 28: 'crlf', 29: 'lctrl', 30: 'a', 31: 's',
    32: 'd', 33: 'f', 34: 'g', 35: 'h', 36: 'j', 37: 'k', 38: 'l', 39: ';',
    40: '"', 41: '`', 42: 'lshft', 43: '\\', 44: 'z', 45: 'x', 46: 'c',
    47: 'v', 48: 'b', 49: 'n', 50: 'm', 51: ',', 52: '.', 53: '/',
    54: 'rshft', 56: 'lalt', 100: 'ralt'
}

MODHEX_CHARS = [
    'l', 'n', 'r', 't', 'u', 'v', 'c', 'b',
    'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k',
]


def initialize_gpio():
    gpio.setmode(gpio.BOARD)


def finalize_gpio():
    gpio.cleanup()


class YubiKey():
    __input_device = None
    __gpio_pin = None
    __press_duration = None
    __release_duration = None
    __read_timeout = None
    __click_and_read_retries = None
    __last_otp = None
    __interupt_read = None
    semaphore = None

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
        self.semaphore = Semaphore()

    def __del__(self):
        self.__input_device.ungrab()
        self.__input_device.close()

    def __str__(self):
        return 'YubiKey(input_device={}, gpio_pin={})'.format(
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
                    key = SCANCODES.get(data.scancode, None)
                    if key == 'crlf':
                        done = True
                        break
                    elif len(key) == 1 and key in MODHEX_CHARS:
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


def authenticated(function):

    @wraps(function)
    def decorated(*args, **kwargs):
        token = None
        if 'X-Auth-Token' in request.headers:
            token = request.headers['x-auth-token']
        if not token:
            return make_response(
                jsonify({'message': 'No authentication token provided.'}),
                HTTPStatus.UNAUTHORIZED
            )
        if ('AUTH_TOKENS' in app.config
                and token not in app.config['AUTH_TOKENS']):
            return make_response(
                jsonify({'message': 'Authentication token invalid.'}),
                HTTPStatus.UNAUTHORIZED
            )
        return function(*args, **kwargs)

    return decorated


class OTP(Resource):
    yubikey = None

    def __init__(self, yubikey):
        self.yubikey = yubikey

    @authenticated
    def get(self):
        otp = None
        self.yubikey.semaphore.acquire()
        try:
            otp = self.yubikey.click_and_read()
        except Exception as exception:
            print(f'{argv[0]}: error: could not click and read YubiKey, ' +
                  f'due to: {exception}',
                  file=stderr)
        self.yubikey.semaphore.release()
        return make_response(
            jsonify({'otp': otp}),
            HTTPStatus.OK
        )


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
        YubiPi is a project to take the burden of pressing a YubiKey manually
        of you, first and formost for automating things. For that the YubiKey
        is connected to a Raspberry Pi via USB and with its touch sensor
        connected to the GPIO pins over a small circuit. This program is then
        used to trigger the YubiKey and retrieve the outputted
        One-Time-Password.
        ''',
        # It can also serve in REST-API fashion, to make
        # the YubiKey available remotely.
        # ''',
        formatter_class=ArgumentDefaultsHelpFormatter,
    )

    # TODO do more thorough argument checks
    parser.add_argument('-d',
                        '--device',
                        type=FileType('r'),
                        default=None,
                        help='''Input device file of the YubiKey. If not
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
    parser.add_argument('-T',
                        '--timeout',
                        type=float,
                        default=3,
                        help='''Timeout when trying to read from the YubiKey
                        in seconds. Note that the sum of press and release
                        duration is the lower boundary for the timeout, even
                        if you can specify a lower one.''',
                        )
    parser.add_argument('-r',
                        '--retries',
                        type=int,
                        default=2,
                        help='''Number of retries when clicking and reading
                        the YubiKey fails''',
                        )
    parser.add_argument('-S',
                        '--press-duration',
                        type=float,
                        default=0.5,
                        help='''Minimum duration between pressing and
                        releasing the YubiKey touch sensor''',
                        )
    parser.add_argument('-R',
                        '--release-duration',
                        type=float,
                        default=0.5,
                        help='''Minimum duration between releasing and
                        pressing the YubiKey touch sensor''',
                        )
    parser.add_argument('-s',
                        '--server',
                        action='store_true',
                        default=False,
                        help='''Run the program in REST API server mode. It
                        will listen for GET / with a valid authentication
                        token and return an OTP.''',
                        )
    parser.add_argument('-t',
                        '--tokens',
                        nargs='*',
                        help='List of authentication tokens for the REST API',
                        )
    parser.add_argument('-H',
                        '--host',
                        type=str,
                        default='127.0.0.1',
                        help='Host address for the API to listen on',
                        )
    parser.add_argument('-P',
                        '--port',
                        type=int,
                        default=5000,
                        help='Port for the API to listen on',
                        )
    parser.add_argument('-v',
                        '--verbose',
                        dest='verbosity',
                        action='count',
                        default=0,
                        help='''Verbosity, can be given multiple times to set
                             the log level (0: error, 1: warn, 2: info, 3:
                             debug)''',
                        )

    return parser


def parse_args(parser):
    autocomplete(parser)
    args = parser.parse_args()

    args.verbosity = min(args.verbosity, len(LOG_LEVELS)-1)

    return args


def setup_logging(args):
    logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s',
                        level=LOG_LEVELS[args.verbosity])


def main():
    parser = setup_parser()
    args = parse_args(parser)
    setup_logging(args)
    logging.debug(f'commandline arguments: {args}')

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

    if args.server:
        global app
        app = Flask(__name__)
        api = Api(app)
        app.config['SECRET_KEY'] = token_hex(32)

    initialize_gpio()

    yubikey = YubiKey(
        input_device=device,
        gpio_pin=args.pin,
        read_timeout=args.timeout,
        click_and_read_retries=args.retries,
        press_duration=args.press_duration,
        release_duration=args.release_duration,
    )

    if args.server:
        try:
            api.add_resource(OTP, '/',
                             resource_class_kwargs={'yubikey': yubikey})
            app.config['AUTH_TOKENS'] = args.tokens if args.tokens else []
            # TODO do we still need this? maybe for debugging
            # app.run(debug=False, host=args.host, port=args.port)
            serve(app, host=args.host, port=args.port, threads=1)
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
