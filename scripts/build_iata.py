"""Build speedward/core/iata.json: airport codes used by CDNs to name their edge locations.

Cloudflare ends every `cf-ray` response header with the IATA code of the data centre that answered
(observed: `cf-ray: a53cca1a6ccf518e-TXL`, see docs/globalping-api-notes.md). This table turns that
code into a city and a map position, offline.

Source: OurAirports (https://ourairports.com/data/), public domain.
CSV: https://davidmegginson.github.io/ourairports-data/airports.csv
Run once (offline afterwards): python -I scripts/build_iata.py airports.csv

The code list and the city names below are curated by hand: only major airports whose code, city
and country are certain. Coordinates come from the CSV (rounded to 2 decimals, city level), and each
row's country must match the CSV or the build stops. CLOSED holds codes that CDNs still use after the
airport closed (so the CSV no longer lists them); their positions are the former airport's.
"""
import csv
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "speedward" / "core" / "iata.json"

# code: (city shown to people, ISO country)
CODES = {
    # North America
    "ATL": ("Atlanta", "US"), "BOS": ("Boston", "US"), "ORD": ("Chicago", "US"), "DFW": ("Dallas", "US"),
    "DEN": ("Denver", "US"), "DTW": ("Detroit", "US"), "IAH": ("Houston", "US"), "IND": ("Indianapolis", "US"),
    "MCI": ("Kansas City", "US"), "LAS": ("Las Vegas", "US"), "LAX": ("Los Angeles", "US"),
    "MEM": ("Memphis", "US"), "MIA": ("Miami", "US"), "MSP": ("Minneapolis", "US"), "BNA": ("Nashville", "US"),
    "EWR": ("Newark", "US"), "JFK": ("New York", "US"), "OMA": ("Omaha", "US"), "PHL": ("Philadelphia", "US"),
    "PHX": ("Phoenix", "US"), "PIT": ("Pittsburgh", "US"), "PDX": ("Portland", "US"), "RDU": ("Raleigh", "US"),
    "SMF": ("Sacramento", "US"), "SLC": ("Salt Lake City", "US"), "SAN": ("San Diego", "US"),
    "SFO": ("San Francisco", "US"), "SJC": ("San Jose", "US"), "SEA": ("Seattle", "US"),
    "STL": ("St. Louis", "US"), "TPA": ("Tampa", "US"), "IAD": ("Washington", "US"), "CLT": ("Charlotte", "US"),
    "CLE": ("Cleveland", "US"), "CMH": ("Columbus", "US"), "JAX": ("Jacksonville", "US"), "MCO": ("Orlando", "US"),
    "SAT": ("San Antonio", "US"), "AUS": ("Austin", "US"), "ABQ": ("Albuquerque", "US"), "BUF": ("Buffalo", "US"),
    "RIC": ("Richmond", "US"), "HNL": ("Honolulu", "US"), "ANC": ("Anchorage", "US"),
    "OKC": ("Oklahoma City", "US"), "MKE": ("Milwaukee", "US"), "BWI": ("Baltimore", "US"),
    "FLL": ("Fort Lauderdale", "US"),
    "YYZ": ("Toronto", "CA"), "YUL": ("Montreal", "CA"), "YVR": ("Vancouver", "CA"), "YYC": ("Calgary", "CA"),
    "YEG": ("Edmonton", "CA"), "YOW": ("Ottawa", "CA"), "YWG": ("Winnipeg", "CA"), "YHZ": ("Halifax", "CA"),
    "YXE": ("Saskatoon", "CA"), "YQB": ("Quebec City", "CA"),
    "MEX": ("Mexico City", "MX"), "GDL": ("Guadalajara", "MX"), "MTY": ("Monterrey", "MX"),
    "QRO": ("Queretaro", "MX"), "CUN": ("Cancun", "MX"), "TIJ": ("Tijuana", "MX"),
    # Central America and the Caribbean
    "GUA": ("Guatemala City", "GT"), "SJO": ("San Jose", "CR"), "PTY": ("Panama City", "PA"),
    "SAL": ("San Salvador", "SV"), "TGU": ("Tegucigalpa", "HN"), "MGA": ("Managua", "NI"),
    "SDQ": ("Santo Domingo", "DO"), "SJU": ("San Juan", "PR"), "KIN": ("Kingston", "JM"),
    "POS": ("Port of Spain", "TT"), "NAS": ("Nassau", "BS"), "HAV": ("Havana", "CU"),
    "BGI": ("Bridgetown", "BB"), "CUR": ("Willemstad", "CW"),
    # South America
    "GRU": ("Sao Paulo", "BR"), "GIG": ("Rio de Janeiro", "BR"), "BSB": ("Brasilia", "BR"),
    "CNF": ("Belo Horizonte", "BR"), "POA": ("Porto Alegre", "BR"), "CWB": ("Curitiba", "BR"),
    "FOR": ("Fortaleza", "BR"), "REC": ("Recife", "BR"), "SSA": ("Salvador", "BR"), "MAO": ("Manaus", "BR"),
    "BEL": ("Belem", "BR"), "FLN": ("Florianopolis", "BR"), "VCP": ("Campinas", "BR"),
    "EZE": ("Buenos Aires", "AR"), "COR": ("Cordoba", "AR"), "SCL": ("Santiago", "CL"), "LIM": ("Lima", "PE"),
    "BOG": ("Bogota", "CO"), "MDE": ("Medellin", "CO"), "UIO": ("Quito", "EC"), "GYE": ("Guayaquil", "EC"),
    "CCS": ("Caracas", "VE"), "ASU": ("Asuncion", "PY"), "MVD": ("Montevideo", "UY"), "LPB": ("La Paz", "BO"),
    "VVI": ("Santa Cruz", "BO"), "PBM": ("Paramaribo", "SR"), "GEO": ("Georgetown", "GY"),
    "CAY": ("Cayenne", "GF"),
    # Europe
    "LHR": ("London", "GB"), "MAN": ("Manchester", "GB"), "EDI": ("Edinburgh", "GB"), "DUB": ("Dublin", "IE"),
    "ORK": ("Cork", "IE"), "AMS": ("Amsterdam", "NL"), "BRU": ("Brussels", "BE"), "CDG": ("Paris", "FR"),
    "MRS": ("Marseille", "FR"), "LYS": ("Lyon", "FR"), "NCE": ("Nice", "FR"), "TLS": ("Toulouse", "FR"),
    "BOD": ("Bordeaux", "FR"), "FRA": ("Frankfurt", "DE"), "MUC": ("Munich", "DE"), "BER": ("Berlin", "DE"),
    "HAM": ("Hamburg", "DE"), "DUS": ("Dusseldorf", "DE"), "STR": ("Stuttgart", "DE"), "CGN": ("Cologne", "DE"),
    "ZRH": ("Zurich", "CH"), "GVA": ("Geneva", "CH"), "VIE": ("Vienna", "AT"), "PRG": ("Prague", "CZ"),
    "WAW": ("Warsaw", "PL"), "BUD": ("Budapest", "HU"), "OTP": ("Bucharest", "RO"), "SOF": ("Sofia", "BG"),
    "BEG": ("Belgrade", "RS"), "ZAG": ("Zagreb", "HR"), "LJU": ("Ljubljana", "SI"), "ATH": ("Athens", "GR"),
    "SKG": ("Thessaloniki", "GR"), "IST": ("Istanbul", "TR"), "ESB": ("Ankara", "TR"), "MAD": ("Madrid", "ES"),
    "BCN": ("Barcelona", "ES"), "LIS": ("Lisbon", "PT"), "OPO": ("Porto", "PT"), "MXP": ("Milan", "IT"),
    "FCO": ("Rome", "IT"), "PMO": ("Palermo", "IT"), "CPH": ("Copenhagen", "DK"), "ARN": ("Stockholm", "SE"),
    "GOT": ("Gothenburg", "SE"), "OSL": ("Oslo", "NO"), "HEL": ("Helsinki", "FI"), "RIX": ("Riga", "LV"),
    "TLL": ("Tallinn", "EE"), "VNO": ("Vilnius", "LT"), "KBP": ("Kyiv", "UA"),
    "MSQ": ("Minsk", "BY"), "LED": ("St Petersburg", "RU"), "DME": ("Moscow", "RU"), "SVO": ("Moscow", "RU"),
    "KEF": ("Reykjavik", "IS"), "LUX": ("Luxembourg", "LU"), "MLA": ("Malta", "MT"),
    "BTS": ("Bratislava", "SK"), "SKP": ("Skopje", "MK"), "TIA": ("Tirana", "AL"), "SJJ": ("Sarajevo", "BA"),
    "TBS": ("Tbilisi", "GE"), "EVN": ("Yerevan", "AM"), "GYD": ("Baku", "AZ"), "LCA": ("Larnaca", "CY"),
    # Middle East
    "DXB": ("Dubai", "AE"), "AUH": ("Abu Dhabi", "AE"), "DOH": ("Doha", "QA"), "BAH": ("Manama", "BH"),
    "KWI": ("Kuwait City", "KW"), "MCT": ("Muscat", "OM"), "RUH": ("Riyadh", "SA"), "JED": ("Jeddah", "SA"),
    "DMM": ("Dammam", "SA"), "AMM": ("Amman", "JO"), "BEY": ("Beirut", "LB"), "TLV": ("Tel Aviv", "IL"),
    "BGW": ("Baghdad", "IQ"), "EBL": ("Erbil", "IQ"), "IKA": ("Tehran", "IR"),
    # Africa
    "JNB": ("Johannesburg", "ZA"), "CPT": ("Cape Town", "ZA"), "DUR": ("Durban", "ZA"), "LOS": ("Lagos", "NG"),
    "ABV": ("Abuja", "NG"), "ACC": ("Accra", "GH"), "NBO": ("Nairobi", "KE"), "MBA": ("Mombasa", "KE"),
    "ADD": ("Addis Ababa", "ET"), "DAR": ("Dar es Salaam", "TZ"), "EBB": ("Kampala", "UG"),
    "KGL": ("Kigali", "RW"), "CAI": ("Cairo", "EG"), "ALG": ("Algiers", "DZ"), "TUN": ("Tunis", "TN"),
    "CMN": ("Casablanca", "MA"), "DKR": ("Dakar", "SN"), "LAD": ("Luanda", "AO"), "MPM": ("Maputo", "MZ"),
    "HRE": ("Harare", "ZW"), "LUN": ("Lusaka", "ZM"), "GBE": ("Gaborone", "BW"), "WDH": ("Windhoek", "NA"),
    "MRU": ("Mauritius", "MU"), "TNR": ("Antananarivo", "MG"), "RUN": ("Reunion", "RE"),
    "JIB": ("Djibouti", "DJ"), "KRT": ("Khartoum", "SD"), "ABJ": ("Abidjan", "CI"),
    "OUA": ("Ouagadougou", "BF"), "COO": ("Cotonou", "BJ"), "LFW": ("Lome", "TG"), "FIH": ("Kinshasa", "CD"),
    "DLA": ("Douala", "CM"), "BKO": ("Bamako", "ML"),
    # Asia
    "NRT": ("Tokyo", "JP"), "HND": ("Tokyo", "JP"), "KIX": ("Osaka", "JP"), "FUK": ("Fukuoka", "JP"),
    "NGO": ("Nagoya", "JP"), "CTS": ("Sapporo", "JP"), "OKA": ("Naha", "JP"), "ICN": ("Seoul", "KR"),
    "PUS": ("Busan", "KR"), "TPE": ("Taipei", "TW"), "KHH": ("Kaohsiung", "TW"), "HKG": ("Hong Kong", "HK"),
    "MFM": ("Macau", "MO"), "PEK": ("Beijing", "CN"), "PVG": ("Shanghai", "CN"), "CAN": ("Guangzhou", "CN"),
    "SZX": ("Shenzhen", "CN"), "CTU": ("Chengdu", "CN"), "CKG": ("Chongqing", "CN"), "XIY": ("Xi'an", "CN"),
    "WUH": ("Wuhan", "CN"), "HGH": ("Hangzhou", "CN"), "NKG": ("Nanjing", "CN"), "ULN": ("Ulaanbaatar", "MN"),
    "SIN": ("Singapore", "SG"), "KUL": ("Kuala Lumpur", "MY"), "PEN": ("Penang", "MY"),
    "JHB": ("Johor Bahru", "MY"), "BKK": ("Bangkok", "TH"), "CNX": ("Chiang Mai", "TH"),
    "SGN": ("Ho Chi Minh City", "VN"), "HAN": ("Hanoi", "VN"), "DAD": ("Da Nang", "VN"),
    "PNH": ("Phnom Penh", "KH"), "VTE": ("Vientiane", "LA"), "RGN": ("Yangon", "MM"), "MNL": ("Manila", "PH"),
    "CEB": ("Cebu", "PH"), "CGK": ("Jakarta", "ID"), "SUB": ("Surabaya", "ID"), "DPS": ("Denpasar", "ID"),
    "BWN": ("Bandar Seri Begawan", "BN"), "DEL": ("Delhi", "IN"), "BOM": ("Mumbai", "IN"),
    "MAA": ("Chennai", "IN"), "BLR": ("Bangalore", "IN"), "HYD": ("Hyderabad", "IN"), "CCU": ("Kolkata", "IN"),
    "AMD": ("Ahmedabad", "IN"), "COK": ("Kochi", "IN"), "PNQ": ("Pune", "IN"), "NAG": ("Nagpur", "IN"),
    "JAI": ("Jaipur", "IN"), "LKO": ("Lucknow", "IN"), "KHI": ("Karachi", "PK"), "LHE": ("Lahore", "PK"),
    "ISB": ("Islamabad", "PK"), "DAC": ("Dhaka", "BD"), "CGP": ("Chittagong", "BD"), "KTM": ("Kathmandu", "NP"),
    "CMB": ("Colombo", "LK"), "MLE": ("Male", "MV"), "TAS": ("Tashkent", "UZ"), "ALA": ("Almaty", "KZ"),
    "NQZ": ("Astana", "KZ"), "DYU": ("Dushanbe", "TJ"),
    # Oceania
    "SYD": ("Sydney", "AU"), "MEL": ("Melbourne", "AU"), "BNE": ("Brisbane", "AU"), "PER": ("Perth", "AU"),
    "ADL": ("Adelaide", "AU"), "CBR": ("Canberra", "AU"), "HBA": ("Hobart", "AU"), "DRW": ("Darwin", "AU"),
    "AKL": ("Auckland", "NZ"), "WLG": ("Wellington", "NZ"), "CHC": ("Christchurch", "NZ"), "NAN": ("Nadi", "FJ"),
    "NOU": ("Noumea", "NC"), "PPT": ("Papeete", "PF"), "GUM": ("Guam", "GU"), "POM": ("Port Moresby", "PG"),
}

# Closed airports whose codes Cloudflare still uses (observed: "-TXL" for Berlin). Position: the
# former airport, written by hand.
CLOSED = {"TXL": ("Berlin", "DE", 52.56, 13.29)}


def main(src):
    found = {}
    with open(src, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = (row.get("iata_code") or "").strip()
            if code in CODES and row.get("type") in ("large_airport", "medium_airport"):
                found.setdefault(code, []).append(row)
    out, problems = {}, []
    for code, (city, country) in sorted(CODES.items()):
        rows = [r for r in found.get(code, []) if r["iso_country"] == country]
        if len(rows) != 1:
            problems.append(f"{code}: {len(rows)} matching rows for {country}")
            continue
        lat, lon = float(rows[0]["latitude_deg"]), float(rows[0]["longitude_deg"])
        out[code] = {"city": city, "country": country, "lat": round(lat, 2), "lon": round(lon, 2)}
    for code, (city, country, lat, lon) in CLOSED.items():
        if code in out:
            problems.append(f"{code}: listed as closed but still in the source")
        out[code] = {"city": city, "country": country, "lat": lat, "lon": lon}
    if problems:
        sys.exit("\n".join(problems))
    source = ("OurAirports (https://ourairports.com/data/), public domain; codes and city names "
              "curated by hand; built by scripts/build_iata.py")
    lines = [f"  {json.dumps(code)}: {json.dumps(entry, ensure_ascii=True)}" for code, entry in sorted(out.items())]
    OUT.write_text('{\n"source": ' + json.dumps(source) + ',\n"codes": {\n' + ",\n".join(lines) + "\n}}\n")
    print(f"wrote {len(out)} codes to {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
