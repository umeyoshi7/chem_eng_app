WATER = {"name": "Water", "thermo_id": "water", "mw": 18.015,
         "density_a": 1.0, "density_b": 0.0}

MISCIBLE_SOLVENTS = [
    {"name": "Ethanol",         "thermo_id": "ethanol",            "mw": 46.068,  "density_a": 0.8064, "density_b": -0.000849},
    {"name": "Methanol",        "thermo_id": "methanol",           "mw": 32.042,  "density_a": 0.8097, "density_b": -0.000960},
    {"name": "Acetone",         "thermo_id": "acetone",            "mw": 58.079,  "density_a": 0.8121, "density_b": -0.001100},
    {"name": "Acetonitrile",    "thermo_id": "acetonitrile",       "mw": 41.052,  "density_a": 0.8004, "density_b": -0.001080},
    {"name": "2-Propanol (IPA)","thermo_id": "isopropanol",        "mw": 60.094,  "density_a": 0.8032, "density_b": -0.000900},
    {"name": "1-Propanol",      "thermo_id": "1-propanol",         "mw": 60.094,  "density_a": 0.8175, "density_b": -0.000790},
    {"name": "THF",             "thermo_id": "tetrahydrofuran",    "mw": 72.106,  "density_a": 0.9000, "density_b": -0.001200},
    {"name": "DMF",             "thermo_id": "dimethylformamide",  "mw": 73.094,  "density_a": 0.9640, "density_b": -0.000860},
    {"name": "Ethylene glycol", "thermo_id": "ethylene glycol",    "mw": 62.068,  "density_a": 1.1293, "density_b": -0.000650},
    {"name": "1,4-Dioxane",                    "thermo_id": "1,4-dioxane",                    "mw":  88.106, "density_a": 1.0425, "density_b": -0.001130},
    {"name": "Acetic Acid",                    "thermo_id": "acetic acid",                    "mw":  60.052, "density_a": 1.0708, "density_b": -0.001080},
    {"name": "N,N-Dimethylacetamide",          "thermo_id": "dimethylacetamide",              "mw":  87.120, "density_a": 0.9552, "density_b": -0.000930},
    {"name": "Dimethyl Sulfoxide",             "thermo_id": "dimethyl sulfoxide",             "mw":  78.133, "density_a": 1.1164, "density_b": -0.000800},
    {"name": "Ethylene Glycol Monoethyl Ether","thermo_id": "2-ethoxyethanol",                "mw":  90.121, "density_a": 0.9477, "density_b": -0.000900},
    {"name": "Ethylene Glycol Monomethyl Ether","thermo_id": "2-methoxyethanol",              "mw":  76.094, "density_a": 0.9845, "density_b": -0.000990},
    {"name": "Formamide",                      "thermo_id": "formamide",                      "mw":  45.041, "density_a": 1.1474, "density_b": -0.000700},
    {"name": "Formic Acid",                    "thermo_id": "formic acid",                    "mw":  46.026, "density_a": 1.2440, "density_b": -0.001200},
    {"name": "Methyl Acetate",                 "thermo_id": "methyl acetate",                 "mw":  74.079, "density_a": 0.9582, "density_b": -0.001200},
    {"name": "Methyl Ethyl Ketone",            "thermo_id": "methyl ethyl ketone",            "mw":  72.106, "density_a": 0.8270, "density_b": -0.001100},
    {"name": "n-Methyl-2-Pyrrolidone",         "thermo_id": "n-methyl-2-pyrrolidone",         "mw":  99.131, "density_a": 1.0436, "density_b": -0.000780},
    {"name": "Pyridine",                       "thermo_id": "pyridine",                       "mw":  79.101, "density_a": 1.0025, "density_b": -0.001030},
    {"name": "Sulfolane",                      "thermo_id": "sulfolane",                      "mw": 120.170, "density_a": 1.2895, "density_b": -0.000940},
    {"name": "t-Butyl Alcohol",                "thermo_id": "tert-butanol",                   "mw":  74.122, "density_a": 0.7982, "density_b": -0.000880},
    {"name": "Ethylene Glycol Dimethyl Ether", "thermo_id": "1,2-dimethoxyethane",            "mw":  90.121, "density_a": 0.8848, "density_b": -0.001100},
    {"name": "1,3-Dimethylimidazolidin-2-One", "thermo_id": "1,3-dimethyl-2-imidazolidinone", "mw": 114.145, "density_a": 1.0726, "density_b": -0.000750},
]

IMMISCIBLE_SOLVENTS = [
    {"name": "Toluene",                  "thermo_id": "toluene",                  "mw": 92.141,  "density_a": 0.8843, "density_b": -0.000911},
    {"name": "n-Hexane",                 "thermo_id": "hexane",                   "mw": 86.175,  "density_a": 0.6862, "density_b": -0.000902},
    {"name": "n-Heptane",                "thermo_id": "heptane",                  "mw": 100.202, "density_a": 0.6969, "density_b": -0.000870},
    {"name": "Cyclohexane",              "thermo_id": "cyclohexane",              "mw": 84.160,  "density_a": 0.7969, "density_b": -0.000979},
    {"name": "Benzene",                  "thermo_id": "benzene",                  "mw": 78.112,  "density_a": 0.9001, "density_b": -0.001065},
    {"name": "Chloroform",               "thermo_id": "chloroform",               "mw": 119.378, "density_a": 1.5261, "density_b": -0.001890},
    {"name": "Diethyl ether",            "thermo_id": "diethyl ether",            "mw": 74.122,  "density_a": 0.7362, "density_b": -0.001270},
    {"name": "Ethyl acetate",            "thermo_id": "ethyl acetate",            "mw": 88.106,  "density_a": 0.9245, "density_b": -0.001168},
    {"name": "Dichloromethane",          "thermo_id": "dichloromethane",          "mw": 84.933,  "density_a": 1.3688, "density_b": -0.001870},
    {"name": "Methyl isobutyl ketone",   "thermo_id": "methyl isobutyl ketone",   "mw": 100.160, "density_a": 0.8250, "density_b": -0.001000},
    {"name": "n-Butyl acetate",          "thermo_id": "butyl acetate",            "mw": 116.158, "density_a": 0.9133, "density_b": -0.001050},
    {"name": "Isooctane",                        "thermo_id": "isooctane",                  "mw": 114.229, "density_a": 0.7200, "density_b": -0.000810},
    {"name": "Anisole",                          "thermo_id": "anisole",                    "mw": 108.138, "density_a": 1.0122, "density_b": -0.000910},
    {"name": "1-Butanol",                        "thermo_id": "1-butanol",                  "mw":  74.122, "density_a": 0.8256, "density_b": -0.000792},
    {"name": "2-Butanol",                        "thermo_id": "2-butanol",                  "mw":  74.122, "density_a": 0.8230, "density_b": -0.000833},
    {"name": "Chlorobenzene",                    "thermo_id": "chlorobenzene",              "mw": 112.557, "density_a": 1.1258, "density_b": -0.001000},
    {"name": "cis-1,2-Dichloroethylene",         "thermo_id": "cis-1,2-dichloroethylene",   "mw":  96.943, "density_a": 1.3217, "density_b": -0.001900},
    {"name": "Ethyl Formate",                    "thermo_id": "ethyl formate",              "mw":  74.079, "density_a": 0.9392, "density_b": -0.001120},
    {"name": "Isobutyl Acetate",                 "thermo_id": "isobutyl acetate",           "mw": 116.158, "density_a": 0.8912, "density_b": -0.001000},
    {"name": "Isobutyl Alcohol",                 "thermo_id": "isobutanol",                 "mw":  74.122, "density_a": 0.8175, "density_b": -0.000792},
    {"name": "Isopropyl Acetate",                "thermo_id": "isopropyl acetate",          "mw": 102.132, "density_a": 0.8914, "density_b": -0.001030},
    {"name": "Methyl Butyl Ketone",              "thermo_id": "2-hexanone",                 "mw": 100.158, "density_a": 0.8293, "density_b": -0.000900},
    {"name": "Methylcyclohexane",                "thermo_id": "methylcyclohexane",          "mw":  98.186, "density_a": 0.7874, "density_b": -0.000899},
    {"name": "Methyl tert-Butyl Ether",          "thermo_id": "methyl tert-butyl ether",    "mw":  88.148, "density_a": 0.7605, "density_b": -0.001000},
    {"name": "Nitromethane",                     "thermo_id": "nitromethane",               "mw":  61.040, "density_a": 1.1601, "density_b": -0.001150},
    {"name": "Pentane",                          "thermo_id": "pentane",                    "mw":  72.149, "density_a": 0.6475, "density_b": -0.001066},
    {"name": "1-Pentanol",                       "thermo_id": "1-pentanol",                 "mw":  88.148, "density_a": 0.8298, "density_b": -0.000770},
    {"name": "n-Propyl Acetate",                 "thermo_id": "propyl acetate",             "mw": 102.132, "density_a": 0.9078, "density_b": -0.001000},
    {"name": "Tetrahydronaphthalene",            "thermo_id": "tetrahydronaphthalene",      "mw": 132.202, "density_a": 0.9870, "density_b": -0.000840},
    {"name": "Trichloroethylene",                "thermo_id": "trichloroethylene",          "mw": 131.389, "density_a": 1.4982, "density_b": -0.001700},
    {"name": "Triethylamine",                    "thermo_id": "triethylamine",              "mw": 101.190, "density_a": 0.7419, "density_b": -0.000820},
    {"name": "Xylene (o-)",                      "thermo_id": "o-xylene",                   "mw": 106.165, "density_a": 0.8982, "density_b": -0.000907},
    {"name": "Isoamyl Alcohol",                  "thermo_id": "3-methyl-1-butanol",         "mw":  88.148, "density_a": 0.8262, "density_b": -0.000790},
    {"name": "Isopropylbenzene (Cumene)",         "thermo_id": "cumene",                     "mw": 120.191, "density_a": 0.8795, "density_b": -0.000887},
    {"name": "2-Methyltetrahydrofuran",          "thermo_id": "2-methyltetrahydrofuran",    "mw":  86.132, "density_a": 0.8770, "density_b": -0.001090},
    {"name": "Cyclopentyl Methyl Ether",         "thermo_id": "cyclopentyl methyl ether",   "mw": 100.158, "density_a": 0.8787, "density_b": -0.000970},
    {"name": "4-Methyltetrahydropyran",          "thermo_id": "4-methyltetrahydropyran",    "mw": 100.158, "density_a": 0.8711, "density_b": -0.000900,
     "thermo_surrogate": "tetrahydropyran", "unifac_groups": {1: 1, 27: 1, 78: 2, 79: 1}},
]


ALL_SOLVENTS = [WATER] + MISCIBLE_SOLVENTS + IMMISCIBLE_SOLVENTS


def get_solvent_by_name(name: str, pool: list) -> dict:
    for s in pool:
        if s["name"] == name:
            return s
    raise ValueError(f"Solvent '{name}' not found in pool")
