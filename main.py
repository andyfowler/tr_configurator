import argparse
import datetime
import json
import math
import decimal
from copy import deepcopy
from zeep import Client, helpers

system_types = {
    1: "smartnet",
    8: "p25"
}

class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, decimal.Decimal):
            return str(o)
        if isinstance(o, datetime.datetime):
            return o.isoformat()
        return super(DecimalEncoder, self).default(o)

class RR:
    """
    Radio Refrence interface library
    """
    def __init__(self, rr_system_id: str, username: str, password: str):
        """
        Radio Refrence interface library
        """
        self.rr_system_id = rr_system_id
        self.rr_user = username
        self.rr_pass = password

    def fetch_site_data(self, site_numbers):
        """
        Radio Refrence interface library
        """
        # radio reference authentication
        client = Client("http://api.radioreference.com/soap2/?wsdl&v=15&s=rpc")
        auth_type = client.get_type("ns0:authInfo")
        my_auth_info = auth_type(
            username=self.rr_user,
            password=self.rr_pass,
            appKey="c820a9fd-7488-11ec-ba68-0ecc8ab9ccec",
            version="15",
            style="rpc",
        )

        # prompt user for system ID
        system = client.service.getTrsDetails(self.rr_system_id, my_auth_info)
        sysName = system.sName
        sysresult = system.sysid
        sysid = sysresult[0].sysid

        system_json = helpers.serialize_object(system, dict)

        # Read Talkgroup Data for given System ID
        sites_type = client.get_type("ns0:TrsSites")
        sites = sites_type(client.service.getTrsSites(self.rr_system_id, my_auth_info))

        results = {}
        results["sites"] = []
        results["system"] = json.loads(json.dumps(system_json, cls=DecimalEncoder))
        for site in sites:
            for site_number in site_numbers:
                if int(site["siteNumber"]) == int(site_number):
                    _json = helpers.serialize_object(site, dict)
                    results["sites"].append({ "site": site_number, "data": json.loads(json.dumps(_json, cls=DecimalEncoder))})
        return results
                
class tr_autotune:
    # Ya ya... I dont want to always redo the math :|
    class multipliers:
        khz = 1000
        mhz = 1e+6

    def down_convert(self, value, multiplier):
        return (float(value) / multiplier).__round__(4)

    def up_convert(self, value, multiplier):
        return (float(value) * multiplier).__round__(4)

    def clean_frequencies(self, freqs):
        new_freqs = []
        freqs.sort()
        for freq in freqs:
            new_freqs.append(int(self.up_convert(freq, self.multipliers.mhz)))
        return new_freqs

    def validate_coverage(self, radio_list, freq_list):
        results = []
        all_freq_covered = True
        
        for radio in range(1, len(radio_list) + 1):
            covered = False
            for freq in radio_list[radio]["freqs"]:
                if radio_list[radio]["low"]  <= freq <= radio_list[radio]["high"]:
                    covered = True
            results.append({"freq": freq, "covered": covered})

        for result in results:
            if not result["covered"]:
                all_freq_covered = False

        if not all_freq_covered:
            raise ValueError("Not all frequencies are covered!")

        print(f"[+] Validated all {str(len(freq_list))} channels are covered")

                
    def calculate_center(self, lower_freq, upper_freq, system_freqs):
        center = (lower_freq + upper_freq)/2

        rounding_change = 10000.0 # in HZ
        bad_center = False
        for freq in system_freqs:            
            freq_rounded = self.up_convert(freq, self.multipliers.mhz)
            # Check if our center freq is too close
            if freq_rounded - rounding_change <= center <= freq_rounded + rounding_change:
                bad_center = True

        if bad_center:
            center = center + rounding_change

        return center
    ########################################################################

    def find_freqs(self, SYSTEM_FREQ_LIST, SDR_BANDWIDTH=2.048, SPECTRUM_BANDWIDTH=12.5, debug=False ):
        # sort our freqs low to high
        SYSTEM_FREQS = self.clean_frequencies(SYSTEM_FREQ_LIST)

        # Get our bandwith's
        sdr_bandwidth = self.up_convert(SDR_BANDWIDTH, self.multipliers.mhz)
        spectrum_bandwidth = self.up_convert(SPECTRUM_BANDWIDTH, self.multipliers.khz)
        half_spectrum_bandwidth = spectrum_bandwidth / 2

        # get our edge freqs
        lower_freq = SYSTEM_FREQS[0]
        upper_freq = SYSTEM_FREQS[-1]

        lower_edge = lower_freq - half_spectrum_bandwidth 
        upper_edge = upper_freq + half_spectrum_bandwidth

        # Get total bandwidth needed
        total_coverage_bandwidth = (upper_edge + half_spectrum_bandwidth) - (lower_edge - half_spectrum_bandwidth)

        # get radios needed
        sdr_remainder = total_coverage_bandwidth / sdr_bandwidth
        sdr_needed = int(math.ceil(sdr_remainder))

        bandwith_per_sdr = total_coverage_bandwidth / sdr_needed
        #bandwith_per_sdr = spectrum_bandwidth

        leftover_bandwith = (sdr_bandwidth * sdr_needed) - total_coverage_bandwidth

        if debug:
            # Print out info on decoding
            print(f"[+] Highest frequency - {self.down_convert(upper_freq, self.multipliers.mhz)}")
            print(f"[-] Upper Limit - {self.down_convert(upper_edge, self.multipliers.mhz)}")
            print(f"[+] Lowest frequency - {self.down_convert(lower_freq, self.multipliers.mhz)}")
            print(f"[-] Lower Limit - {self.down_convert(lower_edge, self.multipliers.mhz)}")
            print(f"[+] Total bandwidth to cover - {self.down_convert(total_coverage_bandwidth, self.multipliers.mhz)}")
            print(f"[+] Total Leftover SDR bandwidth - {self.down_convert(leftover_bandwith, self.multipliers.mhz)}")
    

        radio_high_freq, indexed_channels, radio_index = 0, 0, 1
        # System Channel count minux one for zero index
        channels = len(SYSTEM_FREQS) - 1

        # Dict to hold our results
        radio_matrixes = {}

        # First system Freq minus half the spectrum BW
        lower_freq = int(SYSTEM_FREQS[0] - half_spectrum_bandwidth)
        # End of the useable radio range accounting for the half_spectrum_bandwidth
        max_sdr_useable_freq = int((lower_edge + half_spectrum_bandwidth) + sdr_bandwidth)

        # While loop to track if we have indexed all channels to radios
        while (indexed_channels < channels):

            # Channel Count
            sdr_channel_count = 0
            # Check if frquencies are near each other and assign to radios
            for freq in SYSTEM_FREQS:
                # If our frequency is within the bandwidth tolerance of the SDR
                if (freq > lower_freq) and (freq < max_sdr_useable_freq):
                    # Checks if we have created the radio in the results dict yet (Avoids a key error)
                    if not radio_index in radio_matrixes:
                        radio_matrixes[radio_index] = {}
                        radio_matrixes[radio_index]["freqs"] = []

                    # Add matched frerquency to our radio's list
                    radio_matrixes[radio_index]["freqs"].append(freq)
                    # set last indexed Freq to our loops value
                    radio_high_freq = freq

                    # Increment our tracker counts for radio channels / Channels accounted for
                    sdr_channel_count += 1
                    indexed_channels += 1            

            # Set high and low and center and channel counts values for each radio
            radio_matrixes[radio_index]["high"] = radio_high_freq
            radio_matrixes[radio_index]["low"] = lower_freq
            radio_matrixes[radio_index]["channels"] = len(radio_matrixes[radio_index]["freqs"])
            radio_matrixes[radio_index]["center"] = int(self.calculate_center(lower_freq, radio_high_freq, SYSTEM_FREQS))

            # incrment our radios - ie The next channel is beyond our bandwidth
            radio_index += 1

            # Check we havent reacherd the end of our channels
            if indexed_channels <=  channels:
                # Set to the next freq in the list minus half the spectrum BW
                lower_freq = int(SYSTEM_FREQS[indexed_channels] - half_spectrum_bandwidth)
                # Set to the max sdr reciveable bandwidth from the lower_freq
                max_sdr_useable_freq = int((lower_freq + half_spectrum_bandwidth) + sdr_bandwidth)
            
        self.validate_coverage(radio_matrixes, SYSTEM_FREQS)
        return radio_matrixes

class trunk_recorder_helper:
    source_template = {
        "center": 0,
        "rate": 0,
        "ppm": 0,
        "gain": 49,
        "agc": True,
        "digitalLevels": 1,
        "digitalRecorders": 4,
        "analogRecorders": 0,
        "driver": "osmosdr",
        "device": "rtl=00000101"
    }
    system_template =  {
        "control_channels": [
        ],
        "type": "",
        "digitalLevels": 1,
        "talkgroupsFile": "",
        "shortName": "",
        "modulation": "",
        "hideEncrypted": False,
        "uploadScript": "",
        "talkgroupDisplayFormat": "id_tag",
        "compressWav": False,
    }
    base = {
        "ver": 2,
        "sources": [         
        ],
        "systems": [           
        ],
        "captureDir": "",
        "logLevel": "info",
        "broadcastSignals": True,
        "frequencyFormat": "mhz"
        }

def main():
    parser = argparse.ArgumentParser(description='Generate TR config with RR data')
    parser.add_argument('-s','--sites', nargs='+', help='Sites to generate configs for. space seperated', required=True)
    parser.add_argument('--system', help='System to generate configs for', required=True)
    parser.add_argument('--sdr_sample_rate', help='The sample rate of the SDRs in MHz', default='2.048')
    parser.add_argument('-g','--sdr_gain_value', help='The SDR gain value', default='49')
    parser.add_argument('--sdr_ppm_value', help='The SDR PPM value', default='0')
    parser.add_argument('--sdr_agc', help='Enable SDR ACG ', action='store_true')
    parser.add_argument('--spectrum_bandwidth', help='The badwith of the channels in Khz', default='12.5')
    parser.add_argument('-o','--output_dir', help='The directory to place the configs', default='')
    parser.add_argument('-u','--username', help='Radio Refrence Username', required=True)
    parser.add_argument('-p','--password', help='Radio Refrence Password', required=True)

    args = parser.parse_args()

    SAMPLE_RATE = float(args.sdr_sample_rate)
    OUTPUT_DIR = args.output_dir
    SITES = args.sites
    SYSTEM = int(args.system)

    SDR_GAIN_VALUE = args.sdr_gain_value
    SDR_PPM_VALUE = args.sdr_ppm_value
    SDR_AGC_VALUE = args.sdr_agc

    RR_USER = args.username
    RR_PASS = args.password

    TR = tr_autotune()

    System = RR(SYSTEM, RR_USER, RR_PASS)
    results = System.fetch_site_data(SITES)


    # Get Sites radio configs and list frequencies and channels
    sites = []
    for site in results["sites"]:
        freqs = [float(freq["freq"]) for freq in site["data"]["siteFreqs"]]
        control_channels = []
        for freq in site["data"]["siteFreqs"]:
            if freq["use"]: control_channels.append(int(TR.up_convert(freq['freq'], TR.multipliers.mhz)))
        sites.append({
            "id": site["data"]["siteNumber"],
            "freqs": freqs,
            "control_channels": control_channels,
            "modulation": site["data"]["siteModulation"]
            })

    for site in sites:
        result = TR.find_freqs(site["freqs"], SAMPLE_RATE, 12.5)
        
        sources = []
        for radio_index in result:
            payload = deepcopy(trunk_recorder_helper.source_template)

            payload["center"] = result[radio_index]["center"]
            payload["rate"] = int(TR.up_convert(SAMPLE_RATE, TR.multipliers.mhz))
            payload["gain"] = int(SDR_GAIN_VALUE)
            payload["ppm"] = int(SDR_PPM_VALUE)
            payload["agc"] = SDR_AGC_VALUE
            payload["digitalRecorders"] = result[radio_index]["channels"]

            sources.append(payload)
        
        system = deepcopy(trunk_recorder_helper.system_template)
        site_type = system_types[results["system"]["sType"]]
        if site_type == "p25":
            if site["modulation"] == "CPQSK":
                modulation = "qpsk"
            else:
                modulation = "fsk4"
        else:
            modulation = "fsk4"

        system["type"] = site_type
        system["modulation"] = modulation
        system["control_channels"].extend(site["control_channels"])

        config = deepcopy(trunk_recorder_helper.base)
        config["systems"].append(system) 
        config["sources"].extend(sources) 
        
        if OUTPUT_DIR:
            filename = f"{OUTPUT_DIR}/{site['id']}.{SYSTEM}.config.json"            
        else:
            filename = f"{site['id']}.{SYSTEM}.config.json"

        with open(filename, 'w') as f:
            json.dump(config, f, indent=4)
        print(f"[+] Wrote config - {filename}")
            

if __name__ == "__main__":
    main()
