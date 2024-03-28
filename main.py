from machine import Pin, ADC, I2C
from ssd1306 import SSD1306_I2C
from piotimer import Piotimer
from fifo import Fifo
from time import sleep, sleep_ms
from utime import ticks_ms
import framebuf
from hex_numbers import *
import network
import urequests as requests
from statistics import median


class OledScreen(SSD1306_I2C):
    # Mappings for bytearrays in hex_numbers.py
    digit_mappings = {
        0: zero,
        1: one,
        2: two,
        3: three,
        4: four,
        5: five,
        6: six,
        7: seven,
        8: eight,
        9: nine,
    }

    def __init__(self, width, height, i2c, img_width, img_height, addr=60, external_vcc=False):
        self.screen_width = 128
        self.screen_height = 64
        # Size of big digits in hex_numbers.py
        self.img_width = img_width
        self.img_height = img_height
        # Define center of the screen
        self.start_x = int((self.screen_width - (3*self.img_width)) / 2)
        self.start_y = int((self.screen_height - self.img_height) / 2)
        # Define number x, y
        self.numbers_xy = [{'x': self.start_x, 'y': self.start_y}, {'x': self.start_x + self.img_width +1, 
                            'y': self.start_y}, {'x': self.start_x + 2*self.img_width+1, 'y': self.start_y}]
        # Define txt 'bmp' x, y
        self.bpm_xy = [{'x': self.start_x + self.img_width+1, 'y': self.start_y + self.img_height - 10}, 
                       {'x': self.start_x + 2*self.img_width +1, 'y': self.start_y + self.img_height - 10}, 
                       {'x': self.start_x + 3*self.img_width+1, 'y': self.start_y + self.img_height - 10}]
        super().__init__(width, height, i2c, addr, external_vcc)

    def update(self, number):
        number = [int(d) for d in str(number)]
        self.fill(0)
        for i, digit in enumerate(number):
            oled_digit = self.digit_mappings[digit]
            oled_xy = self.numbers_xy[i]
            active = framebuf.FrameBuffer(
                oled_digit, 25, 35, framebuf.MONO_HLSB)
            self.blit(active, oled_xy['x'], oled_xy['y'])
        self.text('BPM', self.bpm_xy[len(number)-1]
                  ['x'], self.bpm_xy[len(number)-1]['y'])
        pulse_sensor.led.on()
        sleep_ms(50)
        pulse_sensor.led.off()
        self.show()

    def stopped(self):
        self.fill(0)
        self.text('Stopped', 1, 1)
        self.text('Press rot push', 1, 21)
        self.text('button to start', 1, 31)
        self.show()

class RotaryKnob():
    def __init__(self, rot_a_pin: int, rot_b_pin: int, rot_push_pin: int) -> None:
        self.rot_a = Pin(rot_a_pin, Pin.IN, Pin.PULL_UP)
        self.rot_b = Pin(rot_b_pin, Pin.IN, Pin.PULL_UP)
        self.rot_push = Pin(rot_push_pin, Pin.IN, Pin.PULL_UP)
        self.debounce_time = 0
        self.mode = False

    def change_screen(self, tid):
        # Show cubios analysis 
        # Wlan_found in case rot activates before wlan initialization 
        if not self.rot_b.value() and pulse_sensor.wlan_found and not pulse_sensor.offline:
            pulse_sensor.stress_recovery()

    def toggle_on_off(self, tid):
        # Run main program if mode = True
        # Debouncing is to prevent multiple button pushes regitering, aka bouncing
        if ((not self.rot_push.value()) and (ticks_ms()-self.debounce_time) > 1000):
            self.debounce_time=ticks_ms()
            self.mode = not self.mode


class PulseSensor():
    APIKEY = "pbZRUi49X48I56oL1Lq8y8NDjq6rPfzX3AQeNo3a"
    CLIENT_ID = "3pjgjdmamlj759te85icf0lucv"
    CLIENT_SECRET = "111fqsli1eo7mejcrlffbklvftcnfl4keoadrdv1o45vt9pndlef"
    LOGIN_URL = "https://kubioscloud.auth.eu-west-1.amazoncognito.com/login"
    TOKEN_URL = "https://kubioscloud.auth.eu-west-1.amazoncognito.com/oauth2/token"
    REDIRECT_URI = "https://analysis.kubioscloud.com/v1/portal/login"

    def __init__(self, pin: int, fifo_size: int, i2c: object) -> None:
        self.adc = ADC(pin)
        self.dbg = Pin(0, Pin.OUT)
        self.oled = OledScreen(128, 64, i2c, 25, 35)
        self.led = Pin("LED", Pin.OUT)
        self.data_analyzed = False
        self.access_token = False
        self.wlan_found = False
        self.offline = False
        self.treshold = 0
        self.max_value = 0
        self.current_peak = 0
        self.previous_peak = 0
        self.data = []
        self.filtered_data = []
        self.cubios_data = []
        self.intervals = []
        self.fifo_size = fifo_size
        self.samples = Fifo(self.fifo_size)
        self.count = 0

    def connection(self, wlan, ssid, pw):
        #Connect to WLAN
        self.wlan = wlan
        self.oled.fill(0)
        if not self.wlan.isconnected() and not self.offline:
            self.wlan = network.WLAN(network.STA_IF)
            self.wlan.active(True)
            self.wlan.connect(ssid, pw)
            for i in range(0, 5):
                if self.wlan.isconnected():
                    self.wlan_found = True
                    ip = self.wlan.ifconfig()[0]
                    self.oled.fill(0)
                    self.oled.text(f'Connected', 1, 1)
                    self.oled.text(f'IP:', 1, 21)
                    self.oled.text(f'{ip}', 1, 31)
                    self.oled.show()
                    sleep(4)
                    break
                else:
                    self.oled.text('Waiting for', 1, 1)
                    self.oled.text('connection...', 1, 11)
                    self.oled.show()
                    sleep(4)
            if not self.wlan.isconnected():
                self.oled.fill(0)
                self.oled.text(f'No connection', 1, 1)
                self.oled.text(f'Continuing in', 1, 21)
                self.oled.text(f'offline mode', 1, 31)
                self.oled.show()
                # Set offline True not to try connection again
                self.offline = True
                sleep(4)
        else:
            self.wlan_found = True

    def buffer_data(self, tid):
        # Function to fill the buffer with new data
        self.samples.put(self.adc.read_u16())

    def get_data(self):
        # Waiting until the buffer is fully updated and then read the data from the buffer
        while self.count != self.fifo_size:
            if not self.samples.empty():
                self.samples.get()
                self.count += 1
        self.data = list(self.samples.data)
        self.count = 0

    def filter_data(self, window):
        # Moving average
        for i in range(len(self.data) - (window - 1)):
            avg = sum(self.data[i:i+window])/window
            self.filtered_data.append(avg)

    def get_treshold(self):
        # Define treshold between avg and max value
        tresh_list = [self.filtered_data[j] for j in range(len(self.filtered_data) - 1)]
        tresh_list.sort()
        min = tresh_list[0]
        max = tresh_list[-1]
        avg = (min + max) / 2
        self.treshold = (avg + max) / 2

    def save_peak_interval(self):
        for i in range(len(self.filtered_data)-1):
            # If data value is over treshold and lower than next value, save peak and index
            if self.filtered_data[i] > self.treshold and self.filtered_data[i] <= self.filtered_data[i+1]:
                self.max_value = self.filtered_data[i]
                self.current_peak = i
            # If data is under treshold and peak has been detected
            elif self.filtered_data[i] < self.treshold and self.max_value != 0:
                # And atleast one peak has been detected, calculate interval
                if self.previous_peak != 0:
                    interval = (self.current_peak - self.previous_peak)
                    self.intervals.append(interval)
                    self.previous_peak = self.current_peak
                    self.max_value = 0
                # Otherwise set first peak
                else:
                    self.previous_peak = self.current_peak
                    self.max_value = 0

    def avg_bpm(self):
        if len(self.intervals) > 0:
            # Calculate bpm from median
            bpm = int(60/(median(self.intervals)*0.004))
            # Calculate milsecs from bpm
            milsecs = int(bpm / 60 * 1000)
            # If connection has been established add data to list to be analyzed
            if self.wlan.isconnected() and self.access_token != False:
                self.cubios_data.append(milsecs)
            # Update Oled Screen
            if bpm < 300:
                self.oled.update(bpm)
            else:
                self.oled.update(0)

    def get_access_token(self, TOKEN_URL=TOKEN_URL, CLIENT_ID = CLIENT_ID, CLIENT_SECRET=CLIENT_SECRET):
        if self.wlan.isconnected() and not self.access_token:
            try:
                response = requests.post(
                url = TOKEN_URL,
                data = 'grant_type=client_credentials&client_id={}'.format(CLIENT_ID),
                headers = {'Content-Type':'application/x-www-form-urlencoded'},
                auth = (CLIENT_ID, CLIENT_SECRET))
                response = response.json()
                self.access_token =  response["access_token"]
            except:
                self.access_token = False
                self.oled.fill(0)
                self.oled.text(f'Error retrieving', 1, 1)
                self.oled.text(f'access token...', 1, 11)
                self.oled.show()
    
    def analyze_data(self, APIKEY=APIKEY):
        if self.wlan.isconnected() and self.access_token != False:
            data_set = {'type': 'RRI',
                        'data': self.cubios_data,
                        'analysis': {
                        'type': 'readiness' }
                        }
            response = requests.post(
                url = "https://analysis.kubioscloud.com/v2/analytics/analyze",
                headers = { "Authorization": "Bearer {}".format(self.access_token),
                "X-Api-Key": APIKEY },
                json = data_set)
            response = response.json()
            if response['status'] == 'ok':
                sns = round(response['analysis']['sns_index'], 1)
                pns = round(response['analysis']['pns_index'], 1)
                self.analyze_sns(sns)
                self.analyze_pns(pns)
                self.data_analyzed = True
            else:
                self.oled.fill(0)
                self.oled.text(f'Error retrieving', 1, 1)
                self.oled.text(f'analysis...', 1, 11)
                self.oled.show()
                sleep(5)
            #RESET list
            self.cubios_data = []

    def analyze_sns(self, data):
        # Get description for analyzed data based on sns and pns value.
        if -0.5 < data < 0.5:
            self.sns = 'Low'
        elif 0.5 <= data < 5 or -2 >= data >= -0.5:
            self.sns = 'Average'
        elif data >= 5:
            self.sns = 'High'
        else:
            self.sns = 'Above average'

    def analyze_pns(self, data):
        # Get description for analyzed data based on sns and pns value.
        if -0.5 < data < 0.5:
            self.pns = 'Excellent'
        elif 0.5 <= data < 1 or -1 >= data >= -0.5:
            self.pns = 'Good'
        elif 1 <= data < 2 or -2 >= data > -1:
            self.pns = 'Average'
        elif data < -2:
            self.pns = 'Poor'
        else:
            self.pns = 'Above average'

    def stress_recovery(self):
        # Print description on screen upon screen mode change
        if self.data_analyzed:
            self.oled.fill(0)
            self.oled.text('Stress level:', 1, 1)
            self.oled.text('{}'.format(self.sns), 1, 11)
            self.oled.text('Recovery level:', 1, 31)
            self.oled.text('{}'.format(self.pns), 1, 41)
            self.oled.show()
            sleep(4)
        else:
            self.oled.fill(0)
            self.oled.text('Not enough', 1, 1)
            self.oled.text('data to analyze', 1, 11)
            self.oled.text('yet...', 1, 21)
            self.oled.show()
            sleep(4)

    def get_bpm(self):
        # Get data within 3 second window
        self.get_data()
        # Filter data using moving average of 20 samples
        self.filter_data(20)
        # Get treshold of the data
        self.get_treshold()
        # Save peak and calculate iterval
        self.save_peak_interval()
        # Get median of bpm and display info on screen
        self.avg_bpm()
        # Reset data
        self.treshold = 0
        self.data = []
        self.filtered_data = []
        self.max_value = 0
        self.previous_peak = 0
        self.intervals = []


#Constants
SCL = Pin(15)
SDA = Pin(14)
i2c = I2C(1, scl=SCL, sda=SDA, freq=400000)
pulse_sensor = PulseSensor(pin=26, fifo_size=750, i2c=i2c)
rot_knob = RotaryKnob(10, 11, 12)

#Timer and interrupts
timer = Piotimer(mode=Piotimer.PERIODIC, freq=250, callback=pulse_sensor.buffer_data)
rot_knob.rot_a.irq(handler=rot_knob.change_screen, trigger=Pin.IRQ_RISING)
rot_knob.rot_push.irq(handler=rot_knob.toggle_on_off, trigger=Pin.IRQ_FALLING)
wlan = network.WLAN(network.STA_IF)

# Main Program

while True:
    if rot_knob.mode:
        pulse_sensor.connection(wlan, 'KME661-Group7', 'Metropolia123')
        pulse_sensor.get_access_token()
        pulse_sensor.oled.fill(0)
        pulse_sensor.get_bpm()
        # If over 10 heartbeats has been detected, send data to cubios cloud
        # This value should be higher, but set to 10 for demo purposes
        if len(pulse_sensor.cubios_data) > 10:
            pulse_sensor.analyze_data()
    else:
        pulse_sensor.oled.stopped()
