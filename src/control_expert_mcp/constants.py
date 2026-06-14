"""Constants extracted from the UnityProServer type library (PServer.tlb, UDE V2.1 / CE V14)."""

# --- Section / program languages (psr*language) ---
LANGUAGES = {
    "IL": 134,
    "ST": 135,
    "LD": 136,
    "FBD": 137,
    "SFC": 138,
    "LL984": 330,
}
LANGUAGE_NAMES = {v: k for k, v in LANGUAGES.items()}

# --- Project / object export options (bit flags) ---
EXPORT_BASIC = 0
EXPORT_WITH_DFB = 1
EXPORT_WITH_DDT = 2
EXPORT_WITH_SR = 4
EXPORT_WITH_CONF = 128
EXPORT_PROJECT_FULL = EXPORT_WITH_DFB | EXPORT_WITH_DDT | EXPORT_WITH_SR | EXPORT_WITH_CONF  # 135

# --- Variables export options (bit flags) ---
EXPORT_VAR_EDT = 1
EXPORT_VAR_DDT = 2
EXPORT_VAR_DEVICE_DDT = 4
EXPORT_VAR_IODDT = 8
EXPORT_VAR_EFB = 16
EXPORT_VAR_DFB = 32
EXPORT_VAR_ALL = 63

# --- Import options ---
IMPORT_OVERWRITE = 0
IMPORT_KEEP_PREVIOUS = 1
IMPORT_RENAME = 2
IMPORT_OPTIONS = {
    "overwrite": IMPORT_OVERWRITE,
    "keep_existing": IMPORT_KEEP_PREVIOUS,
    "rename": IMPORT_RENAME,
}

# --- Build states ---
BUILD_STATES = {
    0: "not_built",
    1: "built_ok",
    2: "analyzed",
    3: "unknown",
}

# --- Task types ---
TASK_TYPES = {
    "FAST": 3,
    "MAST": 4,
    "AUX0": 5,
    "AUX1": 6,
    "AUX2": 7,
    "AUX3": 8,
    "SAFE": 9,
}

# --- Task scheduling ---
TASK_PERIODIC = 0
TASK_CYCLIC = 1

# --- Variable type categories ---
TYPE_CATEGORIES = {
    0: "unknown",
    1: "EDT",
    2: "DDT_struct",
    3: "DDT_array",
    4: "IODDT",
    5: "EFB",
    6: "DFB",
    7: "anonymous_array",
    8: "any_array",
    9: "any_ref",
    10: "any_ddt_ref",
}

# --- UI show states (psrShowState) ---
SHOW_STATES = {
    "maximize": 1,
    "minimize": 2,
    "restore": 3,
    "show": 4,
    "show_maximized": 5,
    "show_minimized": 6,
    "show_normal": 7,
}

# --- HMI display modes ---
HMI_READ_ONLY = 0
HMI_READ_WRITE = 1

# --- Online / target connection ---
TARGET_PLC = 0
TARGET_SIMULATOR = 1
CONNECTION_MODE = {
    "primary": 1,
    "secondary": 2,
}
CONNECTION_STATES = {
    0: "offline",
    1: "connected_different",
    2: "connected_equal",
}
PLC_STATES = {
    0: "unknown",
    1: "stop",
    2: "run",
    3: "default",
    4: "no_conf",
    5: "halt",
}
PLC_COMMANDS = {
    "stop": 0,
    "init": 1,
    "run": 2,
}
