import logging
import time

from i2c_conn import I2CConn
from retrying import retry


# Registers
HEARTBEAT       = 0x00
STATUS          = 0x01
STATS           = 0x02
TIMER           = 0x03
VOLTAGE         = 0x04
VERSION         = 0x05
PINS            = 0x0A
SLEEP_DURATION  = 0x0B

# Pins
PIN_IN_RPI_PWR       = (1 << 0x00)
PIN_IN_STN_PWR_CTRL  = (1 << 0x01)
PIN_OUT_SW_5V        = (1 << 0x02)
PIN_OUT_SW_3V3       = (1 << 0x03)
PIN_OUT_RPI_SHUTDN   = (1 << 0x04)
PIN_OUT_STN_SLEEP    = (1 << 0x05)
PIN_OUT_STN_RESET    = (1 << 0x06)
PIN_OUT_MODEM_DTR    = (1 << 0x07)

INPUT_PINS = {
    "rpi_pwr":       PIN_IN_RPI_PWR,
    "stn_pwr_ctrl":  PIN_IN_STN_PWR_CTRL
}

OUTPUT_PINS = {
    "sw_5v":       PIN_OUT_SW_5V,
    "sw_3v3":      PIN_OUT_SW_3V3,
    "rpi_shutdn":  PIN_OUT_RPI_SHUTDN,
    "stn_sleep":   PIN_OUT_STN_SLEEP,
    "stn_reset":   PIN_OUT_STN_RESET,
    "modem_dtr":   PIN_OUT_MODEM_DTR
}

# Status register
STATES = {
    0x00: "none",
    0x01: "off",
    0x02: "booting",
    0x03: "on",
    0x04: "sleeping",
    0x05: "hibernating"
}

TRIGGERS = {
    0x00: "none",
    0x01: "spm",
    0x02: "rpi",
    0x03: "stn",
    0x04: "rtc",
    0x05: "acc",
    0x06: "gyro",
    0x07: "modem",
    0x08: "timer",
    0x09: "boot_timeout",
    0x0A: "heartbeat_timeout",
    0xFF: "unknown"
}


log = logging.getLogger(__name__)


class SPM2Conn(I2CConn):

    @retry(stop_max_attempt_number=3, wait_fixed=200)
    def heartbeat(self):
        """
        Send heartbeat.
        """

        res = self.read_block(HEARTBEAT, 1)

        return res

    @retry(stop_max_attempt_number=3, wait_fixed=200)
    def status(self):
        """
        Get current status.
        """

        res = self.read_block(STATUS, 5)

        ret = {
            "current_state": STATES.get(res[0], "invalid"),
            "last_state": {
                "up": STATES.get(res[1], "invalid"),
                "down": STATES.get(res[2], "invalid")
            },
            "last_trigger": {
                "up": TRIGGERS.get(res[3], "invalid"),
                "down": TRIGGERS.get(res[4], "invalid")
            }
        }

        return ret

    @retry(stop_max_attempt_number=3, wait_fixed=200)
    def stats(self):
        """
        Get life cycle statistics.
        """

        res = self.read_block(STATS, 9)

        ret = {
            "completed_cycles": (res[1] << 8) + (res[0] << 0),
            "last_boot": {
                "retries": res[2],
                "duration": (res[6] << 24) + (res[5] << 16) + (res[4] << 8) + (res[3] << 0)
            },
            "forced_shutdowns": (res[8] << 8) + (res[7] << 0)
        }

        return ret

    @retry(stop_max_attempt_number=3, wait_fixed=200)
    def timer(self):
        """
        Get current timer value in milliseconds.
        """

        res = self.read_block(TIMER, 4)

        return (res[3] << 24) + (res[2] << 16) + (res[1] << 8) + (res[0] << 0)

    @retry(stop_max_attempt_number=3, wait_fixed=200)
    def pins(self, toggle=None):
        """
        Get current pin states or toggle output pins.
        """

        if toggle != None:
            val = 0
            for pin in toggle.split(","):
                if not pin in OUTPUT_PINS:
                    raise ValueError("Invalid output pin '{:}'".format(pin))

                val |= OUTPUT_PINS[pin]

            self.write_block(PINS, [val])

        res = self.read_block(PINS, 1)

        ret = {
            "input": {k: bool(res[0] & v) for k, v in INPUT_PINS.iteritems()},
            "output": {k: bool(res[0] & v) for k, v in OUTPUT_PINS.iteritems()}
        }

        return ret

    #@retry(stop_max_attempt_number=3, wait_fixed=200)
    def voltage(self):
        """
        Read current voltage.
        """

        raise NotImplementedError()

        res = self.read_block(VOLTAGE, 2)

        return res

    @retry(stop_max_attempt_number=3, wait_fixed=200)
    def version(self):
        """
        Get firmware version.
        """

        res = self.read_block(VERSION, 2)

        return "{:d}.{:d}".format(*res)

    @retry(stop_max_attempt_number=3, wait_fixed=200)
    def sleep_duration(self, value=None):
        """
        Set sleep duration in milliseconds.
        """

        if value != None:
            self.write_block(SLEEP_DURATION, [(value >> i * 8) & 0xFF for i in range(0, 4)])
        
        res = self.read_block(SLEEP_DURATION, 4)

        return (res[3] << 24) + (res[2] << 16) + (res[1] << 8) + (res[0] << 0)

    def sleep_interval(self, value=None):
        """
        Helper method to set sleep duration in seconds.
        Added for backwards compatibility with SPM 1.0.
        """

        if value != None:

            if not isinstance(value, int):
                raise ValueError("Sleep interval must be an integer value")
            elif value > 86400:
                raise ValueError("Max sleep interval is 24 hours (86400 secs)")

            log.info("Setting sleep interval to %d second(s)", value)

            value *= 1000

        res = self.sleep_duration(value=value)

        return int(res / 1000)

    def restart_3v3(self, toggle_delay=1):
        """
        Helper method to restart the 3V3 power supply.
        """

        # Check current state
        res = self.pins()
        if not res["output"]["sw_3v3"]:
            raise Exception("The 3V3 power supply is currently switched off")

        # Power off
        res = self.pins(toggle="sw_3v3")
        if res["output"]["sw_3v3"]:
            raise Exception("The 3V3 power supply did not switch off")

        # Wait for toggle delay
        time.sleep(toggle_delay)

        # Power on again
        res = self.pins(toggle="sw_3v3")
        if not res["output"]["sw_3v3"]:
            raise Exception("The 3V3 power supply did not switch on again")
