# YubiPi

YubiPi is project for triggering a YubiKey from software and providing the
means to do so remotely. This takes the burden of having to bring it with 
you and pressing the button manually from you, and enables you to easily
automate anything that requires a One-Time Password from the YubiKey.

![YubiPi Demonstration](img/yubipi.gif)

## Quick Start
1. Build the triggering circuit exactly as descriped 
[below](#trigering-circuit).
2. SSH into the Raspberry and go to the cloned repository.
```bash
cd yubipi
```
3. Execute the following commands:  
```bash
sudo pip3 install -r requirements.txt
sudo ln -s "$(pwd)/yubipi.py" /usr/local/bin/yubipi
sudo cp yubipi.sh /etc/default/yubipi
TOKEN=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w ${1:-32} | head -n 1)
sudo sed -i "s/^TOKEN=.*$/TOKEN=${TOKEN}/g" /etc/default/yubipi
sudo cp yubipi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start yubipi
sudo apt update && sudo apt install nginx
sudo systemctl start nginx
sudo cp yubipi-nginx.conf /etc/nginx/sites-available/yubipi
sudo ln -s /etc/nginx/sites-available/yubipi /etc/nginx/sites-enabled/yubipi
sudo systemctl reload nginx
```
In case you need a bit more information or want to alter the setup slightly,
please refer to the more detailed guides below.

4. Now you should be able to reach the API server via HTTPS:
```bash
curl -k https://127.0.0.1:5000/ -H "X-Auth-Token: ${TOKEN}"
```

## Hardware

### Triggering Capacitive Touch Sensors
YubiKeys use capacitive touch sensors. Those measure the capacitance of a
capacitor of which the contact plate is part of. The touch with you finger
causes a measurable change thus triggers the sensor.

There are multiple ways to replicate this. Touching the contact pad with a
large conductive object or grounding the pad shortly could be realized with an
actuator or a relay. Both those components however are a bit clunky and also
rarely part of the electronic hobbyists assortment.

A transistor-based solution seems much more elegant but has a catch.
Transistors are capacitive components which means even in a cut-off state
they will pull some charge from the contact plate. This can easily disturb
the measurement process enough so that the touch sensor won't trigger when
the transistor is activated.

The circuit we are aiming for is this:
<img src="img/yubipi_schem.png" width=600px>

The NPN transistor is switched with the Raspberry Pi's GPIO and the collector
of it is used to pull the contact plate to ground. In this case the
collector-emitter or output capacitance of the transistor is key. It needs to
be very low so it does not pull too much charge in the cut-off state.

### What you need
Unfortunately the popular
[BC337](https://www.futurlec.com/Datasheet/Transistor/BC337.pdf)
has an output capacitance of 15 pF. This is too much for triggering the
YubiKey. A [C1815](https://www.futurlec.com/Datasheet/Transistor/C1815.pdf)
with only 2 pF should work just fine.

All in all you will be needing the following components to build the circuit:

- A YubiKey with a capacitive touch sensor (like a YubiKey 5 NFC)
- A Raspberry Pi with a matching free USB port (like a Raspberry Pi 3B v1.2)
- A breadboard, a few wires, a little bit of tinfoil and tape
- A transistor with a low output capacitance (like a C1815)
- A 10 kOhm resistor and optionally a LED

### Triggering Circuit
The following picture shows an example of how the circuit could be arranged on
a breadboard.

![YubiPi Breadboard](img/yubipi_bb.png)

Note that in case of a C1815 transistor the contact order is ECB (Emitter,
Collector, Base).

The touch sensor plate of the YubiKey is connected to the transistors
collector. Make sure to have a large contact area between wire and the plate
with the tinfoil and some tape.

For good measure you might also want to ground the USB ports casing. Here you
can also use some tinfoil and tape.

## Software

After the hardware part is done, you can login into your Raspberry Pi to
install the software. First clone this repository to an arbitrary location
and enter the folder.

### Installation
The Python dependencies are listed in `requirements.txt`, so you can install
them with:
```bash
sudo pip3 install -r requirements.txt
```
Now link the YubiPi script into a binary folder to make it available via the
`PATH` variable.
```bash
sudo ln -s "$(pwd)/yubipi.py" /usr/local/bin/yubipi
```

### CLI Mode
If you connected the triggering circuit to GPIO 21 (pin 40) you should now be
able to get an OTP with:
```bash
yubipi
```
If you connected the trigger to different pin, you can specify this with the
`-p/--pin` argument:
```bash
yubipi -p 40
```
You can find the pinout for the Raspberry Pi [here](https://pinout.xyz).

The program tries to autodetect the YubiKey. In case multiple are connected
you will be prompted to choose one. To specify it from the start, especially
in cases where the program is unable to identify the YubiKey from the device
name, use the `-d/--device` argument:
```bash
yubipi -d /dev/input/event0
```
To manually identify the YubiKey's input device you can use this command:
```bash
for evdev in $(find /sys/class/input/event* -exec basename {} \;);
    do echo "/dev/input/${evdev} : $(cat /sys/class/input/${evdev}/device/name)";
done
```
For more info on the command line interface check the help with help with
`-h/--help`.

Also note that only one instance of the program can operate on one YubiKey at
a time.

### API Mode
To start the API server use the `-s/--server` argument:
```bash
yubipi -s
```
Everything mentioned about setting the pin and input device applies in this
mode as well.

By default the server will be started on localhost and port 5000. You can
query it for an OTP locally:
```bash
curl http://127.0.0.1:5000/
```
If you want it to listen on a different device and port use the `-H/--host`
and `-P/--port`. For example if you want it to listen on any device on
port 5050:
```bash
yubipi -s -H 0.0.0.0 -P 5050
```
In this case you can also call the server on the local network.

To secure the API endpoint with token authentication you can specify tokes
with the `-t/--token` option:
```bash
yubipi -s -t secrettoken1 secrettoken2
```
Then you have to authenticate when doing a query with one of the given tokens:
```bash
curl http://127.0.0.1:5000/ -H 'X-Auth-Token: secrettoken2'
```
Note that while the server is running you cannot run another instance of the
program on the same YubiKey.

### SystemD Service
To run the API server as SystemD service both a service and environment file
are provided.

First copy the environment file:
```bash
sudo cp yubipi.sh /etc/default/yubipi
```
and modify to your needs. You will definitely want to generate a new random
token:
```bash
TOKEN=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w ${1:-32} | head -n 1)
sudo sed -i "s/^TOKEN=.*$/TOKEN=${TOKEN}/g" /etc/default/yubipi
```
Now copy the service file:
```bash
sudo cp yubipi.service /etc/systemd/system/
```
Reload the SystemD configuration:
```bash
sudo systemctl daemon-reload
```
Now you can start the service:
```bash
sudo systemctl start yubipi
```
You should now be able to reach the API server again with CURL:
```bash
curl http://127.0.0.1:5000/ -H "X-Auth-Token: ${TOKEN}"
```

### HTTPS
For TLS encryption with the Waitress WSGI server most often NGINX is used as a
reverse proxy. First make sure NGINX is installed:
```bash
sudo apt update && sudo apt install nginx
```
Make sure the YubiPi service is up and running:
```bash
sudo systemctl start yubipi
```
Make sure NGINX starts:
```bash
sudo systemctl start nginx
```
Next copy the provided NGINX virtual host config:
```bash
sudo cp yubipi-nginx.conf /etc/nginx/sites-available/yubipi
```
Alter the configuration if necessary, for example to use your own root CA
certificate instead of the default self-signed one, or to configure
different ports.

Enable the site by sym-linking the virtual host configuration file:
```bash
sudo ln -s /etc/nginx/sites-available/yubipi /etc/nginx/sites-enabled/yubipi
```
Now reload NGINX to apply the configuration:
```bash
sudo systemctl reload nginx
```
You should now be able to reach the API server again with CURL but via HTTPS:
```bash
curl -k https://127.0.0.1:5000/ -H "X-Auth-Token: ${TOKEN}"
```
Note the `-k` which we merely use to ignore the security warning because of the
self-signed certificate. If you use a root CA certificate or your own CA this
is not necessary. `${TOKEN}` is again the token configured in 
`/etc/default/yubipi`. Since the virtual host is configured to listen on all
devices, you should now also be able to reach the API server via HTTPS from
a different host on the network like your client machine.

## License
This code is distributed under [GPLv3](LICENSE) license.
