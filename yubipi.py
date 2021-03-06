#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK

import RPi.GPIO as gpio

from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter, FileType
from argcomplete import autocomplete
from evdev import InputDevice, categorize, ecodes, list_devices, KeyEvent
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


# The different log levels the program supports.
LOG_LEVELS = {
    0: logging.ERROR,
    1: logging.WARN,
    2: logging.INFO,
    3: logging.DEBUG,
}


# The Flask app for the REST API mode.
app = None


# The scan codes one could read from an input device, only lowercase though
# because the YubiKey is not going to give us capitals.
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


# The modhex charset the YubiKey uses to encode hexadecimal values.
MODHEX_CHARS = [
    'l', 'n', 'r', 't', 'u', 'v', 'c', 'b',
    'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k',
]


def initialize_gpio():
    """
    Initialize the Raspberry Pi's GPIO.

    This function does the basic setup of the Raspberry Pi's GPIO that needs
    to happen prior to using an instance of YubiKey.

    Parameters
    ----------

    Returns
    -------

    See Also
    --------
    finalize_gpio : Finalize the GPIO.
    YubiKey : YubiKey controller.

    Examples
    --------
    >>> initialize_gpio()
    """
    gpio.setmode(gpio.BOARD)


def finalize_gpio():
    """
    Finalize the Raspberry Pi's GPIO.

    This function does the clean up of the Raspberry Pi's GPIO that needs to
    happen prior to exiting the program, or after any work with a YubiKey
    controller is done for example.

    Parameters
    ----------

    Returns
    -------

    See Also
    --------
    initialize_gpio : Initialize the GPIO.
    YubiKey : YubiKey controller.

    Examples
    --------
    >>> finalize_gpio()
    """
    gpio.cleanup()


class YubiKey():
    """
    A YubiKey controller.

    This class is a controller for a YubiKey in context of the YubiPi project.
    It provides functions to trigger the YubiKey and retrieve the
    One-Time-Password from it.

    Attributes
    ----------
    __input_device : str
        The path of the YubiKey's device file.
    __gpio_pin : int
        The Raspberry Pi's GPIO pin the YubiKey's touch sensor is connected to.
    __press_duration : float
        The duration between press and release of the YubiKey's touch sensor
        in seconds.
    __release_duration : float
        The minimum duration between release and another press of the YubiKey's
        touch sensor in seconds.
    __read_timeout : float
        The timeout for a read attempt of a One-Time-Password.
    __click_and_read_retries : int
        The number of retries when reading from the YubiKey times out.
    __last_otp : str
        The last One-Time-Password that was read from the YubiKey.
    __interrupt_read : bool
        The variable signaling the read to interrupt.
    semaphore : Semaphore
        The semaphore to lock the controller to provide serialization for
        asynchronous codes, like the Flask REST API.

    Methods
    -------
    __init__(self, input_device, gpio_pin, press_duration=0.5,
             release_duration=0.5, read_timeout=3,
             click_and_read_retries=2):
        The constructor initializes the controller.
    __del__(self):
        The destructor cleans up.
    __str__(self):
        This method returns a string representation of the controller.
    press(self):
        This method does a press of the YubiKey's touch sensor.
    release(self):
        This method does a release of the YubiKey's touch sensor.
    click(self):
        This method combines the press and release to simulate a click.
    read(self):
        This method tries to read a One-Time-Password from the YubiKey.
    click_and_read(self):
        This method tries to click the YubiKey and retrieve the
        One-Time-Password.
    """
    __input_device = None
    __gpio_pin = None
    __press_duration = None
    __release_duration = None
    __read_timeout = None
    __click_and_read_retries = None
    __last_otp = None
    __interrupt_read = None
    semaphore = None

    def __init__(self, input_device, gpio_pin, press_duration=0.5,
                 release_duration=0.5, read_timeout=3,
                 click_and_read_retries=2):
        """
        Initialize the YubiKey controller.

        This function initializes a YubiKey controller. It takes and sets all
        the attributes like the path to the input device. It set's up the
        GPIO pin. It opens and grabs the input device. It also creates the
        semaphore.

        Note that initialize_gpio() has to be executed before you can
        initialize a YubiKey controller.

        Parameters
        ----------
        input_device : str
            Path to the device file of the YubiKey.
        gpio_pin : int
            GPIO pin number the triggering circuit is connected to.
        press_duration : float, optional
            Time for which the press is applied during a click
            (default is 0.5).
        release_duration : float, optional
            Time for which the release is applied during a click
            (default is 0.5).
        read_timeout : float, optional
            Duration in seconds after which a read attempt when doing a
            click_and_read (default is 3).
        click_and_read_retries : int, optional
            Number of retries when read attempts time out when doing a
            click_and_read (default is 2).

        Returns
        -------
        YubiKey
            The initialized YubiKey object.

        See Also
        --------
        __del__ : Clean up a YubiKey controller.
        initialize_gpio : Initialize the Raspberry Pi's GPIO.

        Examples
        --------
        >>> initialize_gpio()
        >>> yubikey = YubiKey('/dev/input/event0', 40)
        >>> yubikey
        <yubipi.YubiKey object at 0x75cac7c0>
        """
        self.__input_device = InputDevice(input_device)
        self.__gpio_pin = gpio_pin
        self.__press_duration = press_duration
        self.__release_duration = release_duration
        self.__read_timeout = read_timeout
        self.__click_and_read_retries = click_and_read_retries
        self.__interrupt_read = False
        gpio.setup(self.__gpio_pin, gpio.OUT, initial=gpio.LOW)
        self.__input_device.grab()
        self.semaphore = Semaphore()

    def __del__(self):
        """
        Clean up a YubiKey controller.

        It ungrabs and closes the YubiKey's input device.

        Parameters
        ----------

        Returns
        -------

        See Also
        --------
        __init__ : Initialize a YubiKey controller.

        Examples
        --------
        >>> del yubikey
        """
        self.__input_device.ungrab()
        self.__input_device.close()

    def __str__(self):
        """
        Returns string representation of the YubiKey controller object.

        Parameters
        ----------

        Returns
        -------
        str
            The string representation of the YubiKey controller.

        See Also
        --------

        Examples
        --------
        >>> print(yubikey)
        YubiKey(input_device=/dev/input/event0, gpio_pin=40)
        """
        return 'YubiKey(input_device={}, gpio_pin={})'.format(
            self.__input_device.path,
            self.__gpio_pin
        )

    def press(self):
        """
        Press the YubiKey's touch sensor.

        Parameters
        ----------

        Returns
        -------

        See Also
        --------
        release : Release the YubiKey's touch sensor.

        Examples
        --------
        >>> yubikey.press()
        >>> sleep(1)
        >>> yubikey.release()
        """
        gpio.output(self.__gpio_pin, gpio.HIGH)

    def release(self):
        """
        Release the YubiKey's touch sensor.

        Parameters
        ----------

        Returns
        -------

        See Also
        --------
        press : Press the YubiKey's touch sensor.

        Examples
        --------
        >>> yubikey.press()
        >>> sleep(1)
        >>> yubikey.release()
        """
        gpio.output(self.__gpio_pin, gpio.LOW)

    def click(self):
        """
        Simulate a click of the YubiKey's touch sensor.

        It combines the press and release methods with intermediary sleeps.
        After the press it waits for the number of seconds specified in the
        attribute __press_duration, and after the release it waits for the
        number of seconds specified in the attribute __release_duration.

        Parameters
        ----------

        Returns
        -------

        See Also
        --------
        press : Press the YubiKey's touch sensor.
        release : Release the YubiKey's touch sensor.
        __press_duration : number of seconds to wait after the press.
        __release_duration : number of seconds to wait after the release.

        Examples
        --------
        >>> yubikey.click()
        """
        # We press and wait, and then release and wait, before doing another
        # press, to simulate distinctive clicks.
        #    ____    ____
        # ___|  |____|  |___
        self.press()
        sleep(self.__press_duration)
        self.release()
        sleep(self.__release_duration)

    def read(self):
        """
        Attempt to read from the YubiKey's input device.

        This uses single evdev reads which don't block when there is nothing
        to read. It loops and checks the attribute __interrupt_read to see
        if it should stop. When it reads a character it decodes it and appends
        it to a local variable. Once it read an entire One-Time-Password it
        saves it in the attribute __last_otp and returns it.

        Parameters
        ----------

        Returns
        -------
        str
            The read One-Time-Password.

        See Also
        --------
        __interrupt_read : The variable signaling the read to interrupt.
        __last_otp : The last One-Time-Password that was read from the YubiKey.
        click_and_read : This method tries to click the YubiKey and retrieve
                         the One-Time-Password.

        Examples
        --------
        Note that the following might not return the entire OTP, so normally
        you would use some kind of threading mechanism, as the click_and_read
        method does.
        >>> yubikey.click()
        >>> yubikey.read()
        cccjgjgkhcbbcvchfkfhiiuunbtnvgihdfiktncvlhck
        """
        # temporary variable for the OTP
        otp = ''

        # loop until interrupted
        while not self.__interrupt_read:
            done = False

            # try to read a keyboard input or ignore the block
            try:
                # read potentially multiple keyboard inputs
                for event in self.__input_device.read():
                    # only read key inputs
                    if event.type != ecodes.EV_KEY:
                        continue

                    # categorize input event and only process key down events
                    data = categorize(event)
                    if data.keystate != KeyEvent.key_down:
                        continue

                    # determine the pressed key
                    key = SCANCODES.get(data.scancode, None)

                    # when enter is pressed we are done
                    if key == 'crlf':
                        done = True
                        break
                    # append modhex characters to the OTP
                    elif len(key) == 1 and key in MODHEX_CHARS:
                        otp += key
                    # other things would be invalid input from a YubiKey
                    else:
                        return None
            except BlockingIOError:
                pass

            if done:
                break

        # check the length of the OTP and save in class attribute so
        # we can retrieve it from a different thread
        if len(otp) == 32:
            self.__last_otp = otp

        # also return it, in case this was called synchronously
        return self.__last_otp

    def click_and_read(self):
        """
        This method tries to click the YubiKey and retrieve the
        One-Time-Password.

        It starts a thread that uses the read method to retrieve the OTP,
        while it invokes the click method. It enforces the timeout given in
        the attribute __read_timeout on the read. If it was unsuccessful
        it retries this as many times as specified in the attribute
        __click_and_read_retries.

        Parameters
        ----------

        Returns
        -------
        str
            The read One-Time-Password.

        See Also
        --------
        click : This method combines the press and release to simulate a click.
        read : This method tries to read a One-Time-Password from the YubiKey.
        __read_timeout : The timeout for a read attempt of a One-Time-Password.
        __click_and_read_retries : The number of retries when reading from the
                                   YubiKey times out.

        Examples
        --------
        >>> yubikey.click_and_read()
        cccjgjgkhcbbcvchfkfhiiuunbtnvgihdfiktncvlhck
        """
        # save the previously read OTP so we know if a new one was read
        previous_otp = self.__last_otp

        # retry reading when the attempt times out
        for _ in range(self.__click_and_read_retries + 1):
            # start a read thread
            read_thread = Thread(target=self.read)
            read_thread.start()

            # click the YubiKey
            self.click()

            # calculate remaining timeout
            timeout = max(0, self.__read_timeout - self.__press_duration
                          - self.__release_duration)

            # join and enforce timeout by interrupting the read
            read_thread.join(timeout=timeout)
            self.__interrupt_read = True

            # wait until interrupt done and re-initialize interrupt variable
            while read_thread.is_alive():
                sleep(0.1)
            self.__interrupt_read = False

            # check a new OTP was read and return it
            if self.__last_otp and self.__last_otp != previous_otp:
                return self.__last_otp

        # unable to read anything
        return None


def authenticated(function):
    """
    Decorator protecting Flask Resource endpoints with a token authentication.

    This decorator takes the X-Auth-Token header and checks its presence in the
    AUTH_TOKENS variable of the Flask app configuration. It returns the
    actual endpoint function when the authentication was successful or returns
    an unauthorized HTTP code. If there is nothing given in AUTH_TOKENS it will
    just return endpoint.

    Parameters
    ----------
    function : callable
        The function to be decorated.

    Returns
    -------
    callable
        The function prepended with the authentication mechanism.

    Examples
    --------
    >>> class MyResource(Resource):
    >>>     @authenticated
    >>>     def get(self):
    >>>         return "hello"
    """

    @wraps(function)
    def decorated(*args, **kwargs):
        token = None

        # check if there is any token to check against
        if 'AUTH_TOKENS' in app.config and app.config['AUTH_TOKENS']:

            # get the token from the request headers
            if 'X-Auth-Token' in request.headers:
                token = request.headers['x-auth-token']

            # if no token given return unauthorized
            if not token:
                return make_response(
                    jsonify({'message': 'No authentication token provided.'}),
                    HTTPStatus.UNAUTHORIZED
                )

            # if token not in the authentication tokens return unauthorized
            if token not in app.config['AUTH_TOKENS']:
                return make_response(
                    jsonify({'message': 'Authentication token invalid.'}),
                    HTTPStatus.UNAUTHORIZED
                )

        # if everything fine return the actual endpoint function
        return function(*args, **kwargs)

    # return the decorated function
    return decorated


class OTP(Resource):
    """
    The One-Time-Password resource.

    This class is a Flask resource for YubiKey OTPs in context of the YubiPi
    project. It provides the endpoint for clicking the YubiKey and retrieving
    the One-Time-Password. The endpoint is protected with the authenticated
    decorator.

    Attributes
    ----------
    yubikey : YubiKey
        The YubiKey to click and read the OTP from.

    Methods
    -------
    __init__(self, yubikey):
        The constructor initializes the OTP resource.
    get(self):
        The GET endpoint that does the click and returns the read OTP.

    See Also
    --------
    authenticated : Decorator protecting Flask Resource endpoints with
                    a token authentication.
    """
    yubikey = None

    def __init__(self, yubikey):
        """
        Initialize the OTP resource.

        This function initializes the OTP resource. It takes the YubiKey
        and saves it as attribute.

        Parameters
        ----------
        yubikey : YubiKey
            The YubiKey to click and read the OTP from.

        Returns
        -------
        OTP
            The One-Time-Password resource.

        Examples
        --------
        >>> otp = OTP(yubikey)
        >>> otp
        <yubipi.OTP object at 0x76b6dd60>
        """
        self.yubikey = yubikey

    @authenticated
    def get(self):
        """
        Get a One-Time-Password from the YubiKey.

        This is the GET endpoint of the OTP resource. It clicks the YubiKey,
        retrieves the OTP and returns it.

        Parameters
        ----------

        Returns
        -------
        str
            The One-Time-Password retrieved from the YubiKey.
        """
        otp = None

        # serialize the accesses to the YubiKey
        self.yubikey.semaphore.acquire()

        # attempt to click the YubiKey and read the OTP or throw an error
        try:
            otp = self.yubikey.click_and_read()
        except Exception as exception:
            print(f'{argv[0]}: error: could not click and read YubiKey, ' +
                  f'due to: {exception}',
                  file=stderr)

        # we are done with the YubiKey
        self.yubikey.semaphore.release()

        # return the OTP
        return make_response(
            jsonify({'otp': otp}),
            HTTPStatus.OK
        )


def detect_yubikey_device_file():
    """
    Detect YubiKey device files.

    This function uses evdev to search for input devices those name suggests
    they are a YubiKey. If there are multiple devices the user gets to choose
    with an inquirer form. The resulting device file path is returned.

    Parameters
    ----------

    Returns
    -------
    str
        The device file path of the one detected or chosen YubiKey

    See Also
    --------

    Examples
    --------
    >>> detect_yubikey_device_file()
    '/dev/input/event0'
    """
    # get all the input devices
    input_devices = [InputDevice(path) for path in list_devices()]

    # filter for YubiKeys devices
    yubikey_devices = []
    for device in input_devices:
        if device.name.startswith("Yubico YubiKey") and 'OTP' in device.name:
            yubikey_devices.append(device)

    num_yubikeys = len(yubikey_devices)
    # if there is just one return its device file path
    if num_yubikeys == 1:
        return yubikey_devices[0].path
    # if there are multiple let the user choose one and return that one's
    # device file path
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

    # nothing found
    return None


def setup_parser():
    """
    Setup the argument parser.

    This function creates the argument parser and defines all the
    arguments before returning it.

    Parameters
    ----------

    Returns
    -------
    ArgumentParser
        The argument parser

    See Also
    --------
    parse_args : Parse the command line arguments.

    Examples
    --------
    >>> setup_parser()
    ArgumentParser(prog='', usage=None, description=
    '\n        YubiPi is a project to take the burden of pressing a
    YubiKey manually\n        of you, first and formost for automating
    things. For that the YubiKey\n        is connected to a Raspberry Pi
    via USB and with its touch sensor\n        connected to the GPIO pins
    over a small circuit. This program is then\n        used to trigger
    the YubiKey and retrieve the outputted\n        One-Time-Password
    \n        ', formatter_class=<class
    'argparse.ArgumentDefaultsHelpFormatter'>, conflict_handler='error',
    add_help=True)
    """
    # create the argument parser
    parser = ArgumentParser(
        description='''
        YubiPi is a project to take the burden of pressing a YubiKey manually
        of you, first and formost for automating things. For that the YubiKey
        is connected to a Raspberry Pi via USB and with its touch sensor
        connected to the GPIO pins over a small circuit. This program is then
        used to trigger the YubiKey and retrieve the outputted
        One-Time-Password. It can also serve in REST-API fashion, to make
        the YubiKey available remotely.
        ''',
        formatter_class=ArgumentDefaultsHelpFormatter,
    )

    # define all the arguments
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
    parser.add_argument('-X',
                        '--https',
                        action='store_true',
                        default=False,
                        help='Configure the WSGI server for HTTPS.',
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

    # return the parser
    return parser


def parse_args(parser):
    """
    Parse the command line arguments.

    This function takes the argument parser, parses the arguments, does the
    auto-completion, and some further argument manipulations.

    Parameters
    ----------
    parser : ArgumentParser
        The argparse argument parser.

    Returns
    -------
    Namespace
        The argparse namespace containing the parsed arguments.

    See Also
    --------
    setup_parser : Setup the argument parser.

    Examples
    --------
    >>> parse_args(parser)
    Namespace(device=None, pin=40, timeout=3, retries=2, press_duration=0.5,
    release_duration=0.5, server=False, tokens=None, host='127.0.0.1',
    port=5000, verbosity=0)
    """
    autocomplete(parser)
    args = parser.parse_args()

    args.verbosity = min(args.verbosity, len(LOG_LEVELS)-1)

    return args


def setup_logging(args):
    """
    Setup the logging.

    This functions sets up the YubiPi's logging, so defined the format
    and log level.

    Parameters
    ----------
    args : Namespace
        The argparse namespace.

    Returns
    -------

    See Also
    --------

    Examples
    --------
    >>> setup_logging()
    """
    logging.basicConfig(format='%(asctime)s %(levelname)s %(message)s',
                        level=LOG_LEVELS[args.verbosity])


def main():
    """
    YubiPi's main function.

    This is the main function of the YubiPi program. It first sets up the
    parser, parses the command line arguments and then sets up the logging.
    Then it detects the YubiKey, initializes the Raspberry's GPIO board,
    and creates a YubiKey controller. Depending on the arguments it then
    clicks and reads the YubiKey, or runs in server mode.

    Parameters
    ----------

    Returns
    -------

    See Also
    --------
    setup_parser : Setup the argument parser.
    parse_args : Parse the command line arguments.
    setup_logging : Setup the logging.
    detect_yubikey_device_file : Detect and select the YubiKey device file.
    initialize_gpio : Initialize the Raspberry Pi's GPIO.
    finalize_gpio : Finalize the Raspberry Pi's GPIO.
    YubiKey : A YubiKey controller.
    YubiKey.click_and_read : Click the YubiKey and read the one-time password.
    OTP : A One-Time Password Flask resource.
    """
    # parse the command line arguments and setup the logging
    parser = setup_parser()
    args = parse_args(parser)
    setup_logging(args)
    logging.debug(f'commandline arguments: {args}')

    device = None
    # if the device is specified as argument use that
    if args.device:
        device = args.device.name
    # or detect it
    else:
        device = detect_yubikey_device_file()
    # if no device is found return an error
    if not device:
        print(f'{argv[0]}: error: no yubikey detected or specified.',
              file=stderr)
        exit(1)

    # do the server setup when requested
    if args.server:
        global app
        app = Flask(__name__)
        api = Api(app)
        app.config['SECRET_KEY'] = token_hex(32)
        logging.getLogger('waitress').setLevel(LOG_LEVELS[args.verbosity])

    # initialize the Raspberry Pi's GPIO
    initialize_gpio()

    # create the YubiKey controller
    yubikey = YubiKey(
        input_device=device,
        gpio_pin=args.pin,
        read_timeout=args.timeout,
        click_and_read_retries=args.retries,
        press_duration=args.press_duration,
        release_duration=args.release_duration,
    )

    # in server mode create the OTP resource and start the API server
    # and at the end clean up the Raspberry Pi's GPIO
    if args.server:
        try:
            api.add_resource(OTP, '/',
                             resource_class_kwargs={'yubikey': yubikey})
            app.config['AUTH_TOKENS'] = args.tokens if args.tokens else []
            # app.run(debug=False, host=args.host, port=args.port)
            url_scheme = 'https' if args.https else 'http'
            serve(app, host=args.host, port=args.port, threads=1,
                  url_scheme=url_scheme)
        finally:
            finalize_gpio()
    # in direct mode try to click YubiKey and read the OTP,
    # then clean up and print it
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


# call the main function when this file is executed
if __name__ == '__main__':
    main()
