import streamlit as st
import pandas as pd
import pyotp  # Handles the 6-digit TOTP
import requests # To download the instrument file
from SmartApi import SmartConnect
from datetime import date, timedelta
import uuid # To create unique IDs for legs and groups
import io # For Excel export
import openpyxl # For Excel export
import time # For Auto-Refresh
import json # <-- ADDED
import os   # <-- ADDED

# --- App Config ---
st.set_page_config(
    page_title="Pro Options Dashboard",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Data Persistence Functions ---
# MOVED THIS ENTIRE BLOCK UP
DATA_FILE = "strategy_data.json"

def save_data():
    """Saves strategy groups and trade history to a JSON file."""
    data_to_save = {
        "strategy_groups": st.session_state.strategy_groups,
        "trade_history": st.session_state.trade_history
    }
    try:
        # We need to handle non-serializable types like datetime.date
        def default_converter(o):
            if isinstance(o, (date, pd.Timestamp)): # Handle date and pandas timestamp
                return o.isoformat()
        
        with open(DATA_FILE, "w") as f:
            json.dump(data_to_save, f, indent=4, default=default_converter)
    except Exception as e:
        print(f"Error saving data: {e}") # You can see this in your terminal
        # st.toast(f"Error saving data: {e}", icon="🚨") # Optional: show error in UI

def load_data():
    """Loads data from JSON file into session_state on startup."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                
                # Load strategy groups
                loaded_groups = data.get("strategy_groups", {})
                
                # --- FIX for JSON date strings ---
                # We must convert date strings back to date objects
                for group_id, group in loaded_groups.items():
                    for leg in group.get('legs', []):
                        if 'expiry' in leg and isinstance(leg['expiry'], str):
                            try:
                                # Try parsing as date first
                                leg['expiry'] = date.fromisoformat(leg['expiry'])
                            except (ValueError, TypeError):
                                try:
                                    # Try parsing as full timestamp (from older pandas versions)
                                    leg['expiry'] = pd.to_datetime(leg['expiry']).date()
                                except:
                                    pass # Keep it as string if all conversion fails
                
                st.session_state.strategy_groups = loaded_groups
                st.session_state.trade_history = data.get("trade_history", [])
                
                # Set active_group_id to the first active group, if any
                if not st.session_state.active_group_id:
                    active_groups = [gid for gid, g in loaded_groups.items() if g.get('status', 'active') == 'active']
                    if active_groups:
                        st.session_state.active_group_id = active_groups[0]
                        
        except Exception as e:
            print(f"Error loading data: {e}")
            # If file is corrupt, initialize fresh
            st.session_state.strategy_groups = {}
            st.session_state.trade_history = []
    else:
        # File doesn't exist, start fresh
        st.session_state.strategy_groups = {}
        st.session_state.trade_history = []
# --- END of Data Persistence Functions ---


# --- Session State Initialization ---
if "api_object" not in st.session_state:
    st.session_state.api_object = None
if "access_token" not in st.session_state:
    st.session_state.access_token = None
if "user_profile" not in st.session_state:
    st.session_state.user_profile = None
if "instrument_list" not in st.session_state:
    st.session_state.instrument_list = None
if "feed_token" not in st.session_state:
    st.session_state.feed_token = None
# --- REPLACED old "strategy_groups" and "trade_history" init ---
if "active_group_id" not in st.session_state:
    st.session_state.active_group_id = None
if "current_spot_price" not in st.session_state:
    st.session_state.current_spot_price = 0.0
if "current_chain" not in st.session_state:
    st.session_state.current_chain = pd.DataFrame()
if "atm_strike" not in st.session_state:
    st.session_state.atm_strike = 0.0
if "all_index_prices" not in st.session_state:
    st.session_state.all_index_prices = {
        "NIFTY": 0.0,
        "BANKNIFTY": 0.0,
        "FINNIFTY": 0.0
    }
# --- Load Data on First Run ---
if "data_loaded" not in st.session_state:
    # This function is defined below
    load_data() 
    st.session_state.data_loaded = True # Flag to prevent re-loading

if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False


# --- Static map for index tokens (NFO for options, NSE for spot index) ---
INDEX_MAP = {
    "NIFTY": {"token": "26000", "exchange": "NSE", "symbol": "NIFTY 50", "lot_size": 25, "step": 50},
    "BANKNIFTY": {"token": "26009", "exchange": "NSE", "symbol": "NIFTY BANK", "lot_size": 15, "step": 100},
    "FINNIFTY": {"token": "26037", "exchange": "NSE", "symbol": "NIFTY FIN SERVICE", "lot_size": 25, "step": 50},
}

# --- Data Persistence Functions ---
# THIS BLOCK WAS MOVED UP
# DATA_FILE = "strategy_data.json"
# ...
# ... (all the way down to)
# ...
# --- END of Data Persistence Functions ---


# --- Angel One Credentials (Loaded Securely) ---
try:
    API_KEY = st.secrets["angelone"]["api_key"]
    CLIENT_ID = st.secrets["angelone"]["client_id"]
    PIN = st.secrets["angelone"]["pin"]
    TOTP_SECRET = st.secrets["angelone"]["totp_secret"]
except KeyError:
    st.error("ERROR: Angel One credentials not found in st.secrets. Please check your .streamlit/secrets.toml file.")
    st.stop()

# --- Helper Functions (App Logic) ---

@st.cache_data(ttl=3600) # Cache the instrument list for 1 hour
def fetch_instrument_list():
    """
    Downloads the master list of all tradable instruments from Angel One.
    """
    st.write("Downloading master instrument list...")
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    try:
        response = requests.get(url)
        response.raise_for_status() # Raise error for bad response
        instrument_data = response.json()
        df = pd.DataFrame(instrument_data)
        
        # Clean up the dataframe for easier use
        df['expiry'] = pd.to_datetime(df['expiry']).dt.date
        df['strike'] = pd.to_numeric(df['strike'], errors='coerce') 
        df['strike'] = df['strike'] / 100.0 
        # Set lotsize based on name
        df['lotsize'] = df['name'].map({k: v['lot_size'] for k, v in INDEX_MAP.items()})

        
        return df
    except Exception as e:
        st.error(f"Failed to download instrument list: {e}")
        return None

def refresh_all_index_prices():
    """
    Fetches LTP for all spot indices defined in INDEX_MAP.
    """
    st.write("Refreshing all index prices for monitor...")
    try:
        if st.session_state.api_object is None:
            st.warning("Please log in first.")
            return

        tokens_by_exchange = {}
        for key, details in INDEX_MAP.items():
            exchange = details["exchange"]
            token = details["token"]
            if exchange not in tokens_by_exchange:
                tokens_by_exchange[exchange] = []
            tokens_by_exchange[exchange].append(token)
        
        # Make API Call
        market_data = st.session_state.api_object.getMarketData("FULL", tokens_by_exchange)

        if market_data['status'] and market_data['data']:
            fetched_data = market_data['data'].get('fetched', [])
            
            new_prices = st.session_state.all_index_prices.copy()
            
            for item in fetched_data:
                token = item.get('symbolToken')
                ltp = item.get('ltp')
                if ltp is None:
                    continue
                
                # Find which index this token belongs to
                for index_name, details in INDEX_MAP.items():
                    if details['token'] == token:
                        new_prices[index_name] = ltp
                        break
            
            st.session_state.all_index_prices = new_prices
        else:
            st.warning(f"Could not fetch index data: {market_data.get('message', 'Unknown error')}")

    except Exception as e:
        st.error(f"Error refreshing index prices: {e}")


def login_to_angel():
    """
    Handles the complete Angel One login process using credentials and TOTP.
    """
    try:
        st.session_state.api_object = SmartConnect(API_KEY)
        
        totp = pyotp.TOTP(TOTP_SECRET).now()
        
        data = {
            "clientcode": CLIENT_ID,
            "password": PIN,
            "totp": totp
        }
        
        session_data = st.session_state.api_object.generateSession(
            data["clientcode"], 
            data["password"], 
            data["totp"]
        )
        
        if session_data['status'] and session_data['data']:
            st.session_state.access_token = session_data['data']['jwtToken']
            st.session_state.feed_token = session_data['data']['feedToken']
            st.session_state.user_profile = st.session_state.api_object.getProfile(session_data['data']['refreshToken'])
            st.session_state.instrument_list = fetch_instrument_list()
            
            # --- Load data on login ---
            # This ensures that if the user logs in *after* app start,
            # their data is still loaded. The 'data_loaded' flag prevents
            # this from overwriting memory if already logged in.
            if "data_loaded" not in st.session_state:
                load_data()
                st.session_state.data_loaded = True

            st.success("Login Successful!")
            refresh_all_index_prices()
            st.rerun() # --- FIX: Force a rerun on successful login ---
        else:
            st.error(f"Login Failed: {session_data['message']}")

    except Exception as e:
        st.error(f"An error occurred during login: {e}")

def refresh_all_prices(group_id):
    """
    Fetches LTP for all legs AND the active spot price in a SINGLE API call for a specific group.
    """
    if group_id not in st.session_state.strategy_groups:
        st.error("Strategy group not found for refresh.")
        return
        
    group = st.session_state.strategy_groups[group_id]
    st.write(f"Refreshing prices for {group['name']}...")
    try:
        instrument_name = group['instrument']
        if instrument_name not in INDEX_MAP:
            st.error(f"Invalid instrument: {instrument_name}")
            return

        spot_details = INDEX_MAP[instrument_name]
        
        tokens_by_exchange = {}

        spot_exchange = spot_details["exchange"]
        spot_token = spot_details["token"]
        if spot_exchange not in tokens_by_exchange:
            tokens_by_exchange[spot_exchange] = []
        tokens_by_exchange[spot_exchange].append(spot_token)

        active_legs_exist = False
        for leg in group['legs']:
            if leg['status'] == 'active': # Only fetch for active legs
                active_legs_exist = True
                exchange = leg['exchange']
                token = leg['token']
                if exchange not in tokens_by_exchange:
                    tokens_by_exchange[exchange] = []
                if token not in tokens_by_exchange[exchange]:
                    tokens_by_exchange[exchange].append(token)
        
        if not active_legs_exist:
             st.warning("No active legs to refresh for this strategy.")
             pass

        market_data = st.session_state.api_object.getMarketData("FULL", tokens_by_exchange)

        if market_data['status'] and market_data['data']:
            fetched_data = market_data['data'].get('fetched', [])
            
            for item in fetched_data:
                token = item.get('symbolToken')
                ltp = item.get('ltp')

                if ltp is None:
                    continue

                if token == spot_details['token']:
                    st.session_state.current_spot_price = ltp
                    step = spot_details["step"]
                    st.session_state.atm_strike = round(ltp / step) * step
                
                for leg in group['legs']:
                    if leg['status'] == 'active' and leg['token'] == token:
                        leg['current_ltp'] = ltp
                        break
            st.success(f"Prices updated for {group['name']}!")
            refresh_all_index_prices()
        else:
            st.warning(f"Could not fetch market data: {market_data.get('message', 'Unknown error')}")
            
    except Exception as e:
        st.error(f"Error refreshing prices: {e}")


def s2_from_s1_and_spot(s1, spot, step):
    """Calculates the PR Sundar 'Averaging' strike (S2 = 2*T - S1)."""
    target_spot = round(spot / step) * step
    return (2 * target_spot - s1)

def calculate_group_stats(group, legs_data):
    """
    Calculates combined stats for a group of positions.
    """
    realised_pnl = 0
    unrealised_pnl = 0
    net_delta = 0
    net_theta = 0
    net_credit = 0
    total_short_lots = 0
    weighted_strike_sum = 0
    
    if not group or not legs_data:
        return {
            'total_pnl': 0, 'realised_pnl': 0, 'unrealised_pnl': 0,
            'net_delta': 0, 'net_theta': 0, 'net_credit': 0, 'avg_strike': 0, 'total_lots': 0
        }

    for leg in legs_data:
        lots = leg.get('lots', 1)
        lot_size = leg.get('lot_size', INDEX_MAP.get(group['instrument'], {}).get('lot_size', 25))
        
        entry_premium = pd.to_numeric(leg.get('entry_premium', 0), errors='coerce')
        delta = pd.to_numeric(leg.get('delta', 0), errors='coerce')
        theta = pd.to_numeric(leg.get('theta', 0), errors='coerce')
        
        pnl = pd.to_numeric(leg.get('pnl', 0), errors='coerce')
        if pd.isna(pnl): pnl = 0
        if pd.isna(entry_premium): entry_premium = 0
        
        # Accumulate PnL based on status
        if leg.get('status') == 'closed':
            realised_pnl += pnl
        else:
            unrealised_pnl += pnl

        # Calculate Net Credit only from entry premiums
        if leg.get('side') == 'short':
            net_credit += entry_premium * lots * lot_size
        elif leg.get('side') == 'long':
            net_credit -= entry_premium * lots * lot_size
        
        # Calculate Greeks only for ACTIVE legs
        if leg.get('status') == 'active':
            if not pd.isna(delta):
                if leg.get('side') == 'short':
                    net_delta -= delta * lots * lot_size
                elif leg.get('side') == 'long':
                    net_delta += delta * lots * lot_size
            
            if not pd.isna(theta):
                if leg.get('side') == 'short':
                    net_theta += theta * lots * lot_size
                elif leg.get('side') == 'long':
                    net_theta -= theta * lots * lot_size
            
            # --- THIS IS THE FIX ---
            # We now include all short legs (base, average, extension)
            # EXCLUDING only the 'ff_reference' trades, which are hedges.
            strategy_tag = leg.get('strategy', '')
            if leg.get('side') == 'short' and strategy_tag != 'ff_reference' and leg.get('status') == 'active':
                total_short_lots += lots
                weighted_strike_sum += leg.get('strike', 0) * lots

    
    avg_strike = 0
    if total_short_lots > 0:
        avg_strike = weighted_strike_sum / total_short_lots

    return {
        'total_pnl': realised_pnl + unrealised_pnl,
        'realised_pnl': realised_pnl,
        'unrealised_pnl': unrealised_pnl,
        'net_delta': net_delta,
        'net_theta': net_theta,
        'net_credit': net_credit,
        'avg_strike': avg_strike,
        'total_lots': total_short_lots
    }

def add_leg_to_group(group_id, side, opt_type, strike, symbol, token, exchange, lot_size, strategy_tag="base_trade"):
    """Adds a new option leg."""
    if group_id not in st.session_state.strategy_groups:
        st.error("Strategy group not found.")
        return
    group = st.session_state.strategy_groups[group_id]
    
    new_leg = {
        "id": str(uuid.uuid4()),
        "side": side,
        "type": opt_type,
        "strike": strike,
        "lots": 1,
        "entry_premium": 0.0,
        "current_ltp": 0.0,
        "exit_price": 0.0, 
        "status": "active", 
        "delta": 0.5 if opt_type == 'CE' else -0.5, 
        "theta": 5.0,
        "strategy": strategy_tag, 
        "symbol": symbol,
        "token": token,
        "exchange": exchange,
        "lot_size": lot_size
    }
    
    group['legs'].append(new_leg)
    st.session_state.trade_history.insert(0, f"[{pd.Timestamp.now(tz='Asia/Kolkata').strftime('%H:%M:%S')}] ADD LEG ({group['name']}): {side.upper()} {opt_type} @ {strike} (Tag: {strategy_tag})")    
    st.toast(f"Added {side} {opt_type} @ {strike}. Refresh prices when ready.")
    save_data() # <-- ADDED


def find_strike_row(chain_df, strike):
    """Finds the full row of data for a specific strike in the option chain."""
    if chain_df.empty:
        return None
    row = chain_df[chain_df['strike'] == strike]
    if not row.empty:
        return row.iloc[0]
    else:
        st.warning(f"Strike {strike} not found in the current option chain.")
        return None

# --- Leg Action Handlers ---
def update_leg_details(group_id, leg_id, new_lots, new_entry, new_tag):
    """Updates a single leg's details."""
    if group_id not in st.session_state.strategy_groups:
        return
    group = st.session_state.strategy_groups[group_id]
    
    log_msgs = []
    found = False
    for leg in group['legs']:
        if leg['id'] == leg_id:
            found = True
            if leg['status'] != 'active':
                st.warning("Cannot update a closed leg.")
                return

            try:
                new_lots = int(new_lots)
                new_entry = float(new_entry)
            except ValueError:
                st.error("Lots must be an integer, Entry must be a number.")
                return

            if leg['lots'] != new_lots:
                log_msgs.append(f"LOTS from {leg['lots']} to {new_lots}")
                leg['lots'] = new_lots
            if leg['entry_premium'] != new_entry:
                log_msgs.append(f"ENTRY from {leg['entry_premium']:.2f} to {new_entry:.2f}")
                leg['entry_premium'] = new_entry
            if leg['strategy'] != new_tag:
                log_msgs.append(f"TAG to '{new_tag}'")
                leg['strategy'] = new_tag
            
            if log_msgs:
                st.session_state.trade_history.insert(0, f"[{pd.Timestamp.now(tz='Asia/Kolkata').strftime('%H:%M:%S')}] UPDATE LEG ({group['name']} | {leg['strike']} {leg['type']}): {', '.join(log_msgs)}")
                st.toast(f"Updated {leg['strike']} {leg['type']}")
                save_data() # <-- ADDED
            break
    
    if not found:
        st.error("Leg not found for update.")


def exit_leg(group_id, leg_id):
    """Marks a single leg as 'closed'."""
    if group_id not in st.session_state.strategy_groups:
        return
    group = st.session_state.strategy_groups[group_id]
    
    leg_to_close = None
    for leg in group['legs']:
        if leg['id'] == leg_id:
            leg_to_close = leg
            break
    
    if leg_to_close:
        if leg_to_close['status'] == 'closed':
            st.warning("Leg is already closed.")
            return
            
        leg_to_close['status'] = 'closed'
        leg_to_close['exit_price'] = leg_to_close['current_ltp'] # Lock in the exit price
        st.session_state.trade_history.insert(0, f"[{pd.Timestamp.now(tz='Asia/Kolkata').strftime('%H:%M:%S')}] EXIT LEG ({group['name']}): {leg_to_close['side'].upper()} {leg_to_close['type']} @ {leg_to_close['strike']} at {leg_to_close['exit_price']:.2f}")
        st.toast(f"Exited {leg_to_close['strike']} {leg_to_close['type']}")
        save_data() # <-- ADDED


def add_weekly_hedge(group_id, instrument, weekly_expiry, strike, opt_type):
    """Finds and adds a weekly hedge leg."""
    if group_id not in st.session_state.strategy_groups or st.session_state.instrument_list is None:
        return
    group = st.session_state.strategy_groups[group_id]
    instrument_df = st.session_state.instrument_list

    try:
        hedge_row = instrument_df[
            (instrument_df['name'] == instrument) &
            (instrument_df['expiry'] == weekly_expiry) &
            (instrument_df['strike'] == strike) &
            (instrument_df['symbol'].str.endswith(opt_type))
        ].iloc[0]
        
        add_leg_to_group(
            group_id, 
            "long", 
            opt_type, 
            strike, 
            hedge_row['symbol'], 
            hedge_row['token'], 
            hedge_row['exch_seg'], 
            hedge_row['lotsize'], 
            "weekly_hedge"
        )
        st.success(f"Added {strike} {opt_type} (Weekly Hedge)!")
        # save_data() is already called inside add_leg_to_group
    except Exception as e:
        st.error(f"Could not find weekly hedge for {strike} {opt_type} on {weekly_expiry}. {e}")


# --- Firefighting action functions ---
def firefight_average(group_id, strike):
    chain_df = st.session_state.current_chain
    group = st.session_state.strategy_groups[group_id]
    s2_row = find_strike_row(chain_df, strike)
    if s2_row is not None:
        add_leg_to_group(group_id, "short", "CE", strike, s2_row.symbol_CE, s2_row.token_CE, s2_row.exch_seg_CE, s2_row.lotsize_CE, "ff_average")
        add_leg_to_group(group_id, "short", "PE", strike, s2_row.symbol_PE, s2_row.token_PE, s2_row.exch_seg_PE, s2_row.lotsize_PE, "ff_average")
    st.session_state.trade_history.insert(0, f"[{pd.Timestamp.now(tz='Asia/Kolkata').strftime('%H:%M:%S')}] FIREFIGHT (AVG) ({group['name']}): Added Straddle @ {strike}")
    st.success(f"Firefighting Straddle @ {strike} added.")
    save_data() # <-- ADDED

def firefight_add_reference_trade(group_id, strike, opt_type):
    chain_df = st.session_state.current_chain
    group = st.session_state.strategy_groups[group_id]
    ext_row = find_strike_row(chain_df, strike)
    if ext_row is not None:
        if opt_type == "PE":
            add_leg_to_group(group_id, "short", "PE", strike, ext_row.symbol_PE, ext_row.token_PE, ext_row.exch_seg_PE, ext_row.lotsize_PE, "ff_reference")
        elif opt_type == "CE":
            add_leg_to_group(group_id, "short", "CE", strike, ext_row.symbol_CE, ext_row.token_CE, ext_row.exch_seg_CE, ext_row.lotsize_CE, "ff_reference")
    st.session_state.trade_history.insert(0, f"[{pd.Timestamp.now(tz='Asia/Kolkata').strftime('%H:%M:%S')}] FIREFIGHT (REF) ({group['name']}): Added {opt_type} @ {strike}")
    st.success(f"Firefighting Reference {opt_type} @ {strike} added.")
    save_data() # <-- ADDED

def firefight_shift_base(group_id, atm_strike):
    chain_df = st.session_state.current_chain
    group = st.session_state.strategy_groups.get(group_id)
    if not group:
        st.error("Group not found for shifting.")
        return

    for leg in group['legs']:
        if leg['status'] == 'active':
            leg['status'] = 'closed'
            leg['exit_price'] = leg['current_ltp']

    atm_row = find_strike_row(chain_df, atm_strike)
    if atm_row is not None:
        add_leg_to_group(group_id, "short", "CE", atm_strike, atm_row.symbol_CE, atm_row.token_CE, atm_row.exch_seg_CE, atm_row.lotsize_CE, "base_straddle")
        add_leg_to_group(group_id, "short", "PE", atm_strike, atm_row.symbol_PE, atm_row.token_PE, atm_row.exch_seg_PE, atm_row.lotsize_PE, "base_straddle")
    st.session_state.trade_history.insert(0, f"[{pd.Timestamp.now(tz='Asia/Kolkata').strftime('%H:%M:%S')}] FIREFIGHT (SHIFT) ({group['name']}): Closed active legs. Added new Straddle @ {atm_strike}")
    st.success(f"Base Shifted to new ATM @ {atm_strike}.")
    save_data() # <-- ADDED

def firefight_true_extension(group_id, strike, opt_type):
    chain_df = st.session_state.current_chain
    group = st.session_state.strategy_groups[group_id]
    ext_row = find_strike_row(chain_df, strike)
    if ext_row is not None:
        if opt_type == "PE":
            add_leg_to_group(group_id, "short", "PE", strike, ext_row.symbol_PE, ext_row.token_PE, ext_row.exch_seg_PE, ext_row.lotsize_PE, "ff_extension")
        elif opt_type == "CE":
            add_leg_to_group(group_id, "short", "CE", strike, ext_row.symbol_CE, ext_row.token_CE, ext_row.exch_seg_CE, ext_row.lotsize_CE, "ff_extension")
    st.session_state.trade_history.insert(0, f"[{pd.Timestamp.now(tz='Asia/Kolkata').strftime('%H:%M:%S')}] FIREFIGHT (EXT) ({group['name']}): Added {opt_type} @ {strike}")
    st.success(f"Firefighting Extension {opt_type} @ {strike} added.")
    save_data() # <-- ADDED

def close_all_positions(group_id):
    if group_id not in st.session_state.strategy_groups:
        st.error("Strategy group not found.")
        return
    group = st.session_state.strategy_groups[group_id]
    group_name = group['name']
    
    for leg in group['legs']:
        if leg['status'] == 'active':
            leg['status'] = 'closed'
            leg['exit_price'] = leg['current_ltp']
    
    group['status'] = 'closed'
    st.session_state.active_group_id = None
    
    st.session_state.trade_history.insert(0, f"[{pd.Timestamp.now(tz='Asia/Kolkata').strftime('%H:%M:%S')}] ACTION: Close All Positions executed for {group_name}. Strategy moved to 'Closed'.")
    st.success(f"All positions for {group_name} have been closed.")
    save_data() # <-- ADDED


# --- Multi-strategy UI Handlers ---
def create_new_strategy(name, instrument):
    if not name:
        st.error("Please enter a strategy name.")
        return False 
    group_id = str(uuid.uuid4())
    new_group = {
        "id": group_id,
        "name": name,
        "instrument": instrument,
        "legs": [],
        "buffer": 100, # Default buffer
        "status": "active" 
    }
    st.session_state.strategy_groups[group_id] = new_group
    st.session_state.active_group_id = group_id
    
    st.session_state.trade_history.insert(0, f"[{pd.Timestamp.now(tz='Asia/Kolkata').strftime('%H:%M:%S')}] ACTION: Created new strategy '{name}'.")
    save_data() # <-- ADDED
    return True 

@st.dialog("Create New Strategy")
def new_strategy_dialog():
    with st.form("new_strategy_form"):
        strategy_name = st.text_input("Strategy Name", placeholder="e.g., Nifty Straddle")
        instrument = st.selectbox("Instrument", ["NIFTY", "BANKNIFTY", "FINNIFTY"])
        
        submitted = st.form_submit_button("Create")
        if submitted:
            success = create_new_strategy(strategy_name, instrument)
            if success:
                st.rerun() 

def set_active_group(group_id):
    st.session_state.active_group_id = group_id
    if group_id in st.session_state.strategy_groups:
        group = st.session_state.strategy_groups[group_id]
        if group.get('status') == 'active':
            st.session_state['refresh_on_select'] = group_id


def delete_group(group_id):
    if group_id in st.session_state.strategy_groups:
        group_name = st.session_state.strategy_groups[group_id]['name']
        
        del st.session_state.strategy_groups[group_id]
        
        st.session_state.trade_history.insert(0, f"[{pd.Timestamp.now(tz='Asia/Kolkata').strftime('%H:%M:%S')}] ACTION: Deleted strategy '{group_name}'.")
        if st.session_state.active_group_id == group_id:
            st.session_state.active_group_id = None
        save_data() # <-- ADDED

# --- NEW Function to clear history ---
def clear_trade_history():
    st.session_state.trade_history = []
    save_data()

# --- Excel Export Function ---
def create_excel_export():
    closed_strategies = {gid: g for gid, g in st.session_state.strategy_groups.items() if g.get('status') == 'closed'}
    
    if not closed_strategies:
        st.warning("No closed strategies to export.")
        return None

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        for group_id, group in closed_strategies.items():
            sheet_name = "".join(c for c in group['name'] if c.isalnum() or c in (' ', '_')).rstrip()[:31]
            if not sheet_name:
                sheet_name = f"Strategy_{group_id[:8]}"

            leg_data = []
            for leg in group['legs']:
                entry = leg.get('entry_premium', 0)
                exit_p = leg.get('exit_price', 0) if leg.get('status') == 'closed' else leg.get('current_ltp', 0)
                lots = leg.get('lots', 1)
                lot_size = leg.get('lot_size', INDEX_MAP.get(group['instrument'], {}).get('lot_size', 25))
                pnl = 0
                if leg.get('side') == 'short':
                    pnl = (entry - exit_p) * lots * lot_size
                elif leg.get('side') == 'long':
                    pnl = (exit_p - entry) * lots * lot_size
                
                leg_data.append({
                    "Status": leg.get('status', 'N/A'),
                    "Tag": leg.get('strategy', 'N/A'),
                    "Side": leg.get('side', 'N/A'),
                    "Type": leg.get('type', 'N/A'),
                    "Strike": leg.get('strike', 0),
                    "Lots": lots,
                    "Entry": entry,
                    "Exit": exit_p if leg.get('status') == 'closed' else "N/A (Active)",
                    "PnL": pnl,
                    "Symbol": leg.get('symbol', 'N/A')
                })
            
            df = pd.DataFrame(leg_data)
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    
    output.seek(0)
    return output


# --- Main App UI ---
st.title("🔥 Professional Firefighting Dashboard")

# --- Check for post-selection refresh ---
if 'refresh_on_select' in st.session_state and st.session_state['refresh_on_select']:
    group_id_to_refresh = st.session_state['refresh_on_select']
    if group_id_to_refresh in st.session_state.strategy_groups:
        refresh_all_prices(group_id_to_refresh)
    st.session_state['refresh_on_select'] = None # Clear the flag


if st.session_state.access_token:
    # --- LOGGED IN STATE ---
    
    with st.sidebar:
        try:
            user_name = st.session_state.user_profile['data']['name']
            st.success(f"Welcome, {user_name}!")
        except (TypeError, KeyError):
            st.success("Welcome, User!")
        
        st.markdown("---")
        st.header("📈 Index Monitor")
        idx_c1, idx_c2 = st.columns([2,1])
        with idx_c1:
            st.metric("NIFTY", f"{st.session_state.all_index_prices['NIFTY']:,.2f}")
            st.metric("BANKNIFTY", f"{st.session_state.all_index_prices['BANKNIFTY']:,.2f}")
            st.metric("FINNIFTY", f"{st.session_state.all_index_prices['FINNIFTY']:,.2f}")
        with idx_c2:
            st.button("Refresh", key="refresh_indices", on_click=refresh_all_index_prices, use_container_width=True)
        
        st.markdown("---")
        
        st.header("📋 Strategy Portfolios")
        
        if st.button("Create New Strategy", type="primary", use_container_width=True):
            new_strategy_dialog()
            
        active_strategies = {gid: g for gid, g in st.session_state.strategy_groups.items() if g.get('status', 'active') == 'active'}
        closed_strategies = {gid: g for gid, g in st.session_state.strategy_groups.items() if g.get('status') == 'closed'}

        st.subheader("Active Strategies")
        if not active_strategies:
            st.info("No active strategies.")
        else:
            for group_id, group in active_strategies.items():
                is_active = (st.session_state.active_group_id == group_id)
                label = f"**{group['name']}** ({len([l for l in group['legs'] if l['status'] == 'active'])} legs)"
                if is_active:
                    st.button(f"Viewing: {group['name']}", key=f"view_{group_id}", disabled=True, use_container_width=True)
                else:
                    st.button(f"View: {group['name']}", key=f"select_{group_id}", on_click=set_active_group, args=(group_id,), use_container_width=True)
                        
        st.subheader("Closed Strategies")
        if not closed_strategies:
            st.info("No closed strategies.")
        else:
            excel_data = create_excel_export()
            if excel_data:
                st.download_button(
                    label="📥 Download All Closed (.xlsx)",
                    data=excel_data,
                    file_name=f"closed_strategies_{date.today().strftime('%Y-%m-%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            for group_id, group in closed_strategies.items():
                with st.container(border=True):
                    st.markdown(f"**{group['name']}**")
                    st.caption(f"{group['instrument']} | {len(group['legs'])} total legs")
                    st.button("Delete", key=f"del_{group_id}", on_click=delete_group, args=(group_id,), use_container_width=True)

    # --- Main Page Display ---
    
    tab_dash, tab_chain, tab_history = st.tabs([
        "📈 Dashboard", 
        "⛓️ Option Chain", 
        "📓 Trade History"
    ])

    if st.session_state.active_group_id is None:
        msg = "Please create or select a strategy from the sidebar to begin."
        tab_dash.info(msg)
        tab_chain.info(msg)
    
    elif st.session_state.active_group_id not in st.session_state.strategy_groups:
         st.session_state.active_group_id = None
         st.rerun() 
         
    else:
        active_group_id = st.session_state.active_group_id
        active_group = st.session_state.strategy_groups[active_group_id]
        
        # --- PNL Processing Loop (BUG FIX HERE) ---
        processed_legs = []
        if active_group and active_group['legs']:
            for leg in active_group['legs']:
                new_leg = leg.copy()
                
                if 'lot_size' not in new_leg or pd.isna(new_leg['lot_size']):
                    new_leg['lot_size'] = INDEX_MAP.get(active_group['instrument'], {}).get('lot_size', 25)
                
                entry = pd.to_numeric(new_leg.get('entry_premium', 0), errors='coerce')
                lots = pd.to_numeric(new_leg.get('lots', 1), errors='coerce')
                lot_size = pd.to_numeric(new_leg.get('lot_size', 1), errors='coerce')
                
                ltp = 0.0
                price = 0.0

                if new_leg.get('status') == 'active':
                    ltp = pd.to_numeric(new_leg.get('current_ltp', 0), errors='coerce')
                    price = ltp
                else:
                    price = pd.to_numeric(new_leg.get('exit_price', 0), errors='coerce')
                
                pnl = 0.0
                if not any(pd.isna([entry, price, lots, lot_size])):
                    if new_leg.get('side') == 'short':
                        pnl = (entry - price) * lots * lot_size
                    elif new_leg.get('side') == 'long':
                        pnl = (price - entry) * lots * lot_size
                
                new_leg['pnl'] = pnl
                new_leg['entry_premium'] = entry
                new_leg['lots'] = lots
                new_leg['lot_size'] = lot_size
                new_leg['current_ltp'] = ltp
                new_leg['exit_price'] = price
                
                processed_legs.append(new_leg)
        
        spot = st.session_state.current_spot_price
        atm_strike = st.session_state.atm_strike
        
        stats = calculate_group_stats(active_group, processed_legs)

        # --- TAB 1: Dashboard & Firefighting ---
        with tab_dash:
            st.header(f"📈 Live Dashboard: {active_group['name']}")
            st.caption(f"Instrument: {active_group['instrument']}")
            
            st.markdown("---")
            st.header("Live Metrics")
            m1, m2, m3, m4, m5 = st.columns(5)
            pnl_color = "normal" if stats['total_pnl'] >= 0 else "inverse"
            m1.metric("Total MTM P&L", f"₹{stats['total_pnl']:,.0f}", delta_color=pnl_color)
            m2.metric("Unrealised P&L", f"₹{stats['unrealised_pnl']:,.0f}")
            m3.metric("Realised P&L", f"₹{stats['realised_pnl']:,.0f}")
            m4.metric("Net Delta", f"{stats['net_delta']:,.0f}")
            m5.metric("Net Theta", f"₹{stats['net_theta']:,.0f}")
            
            b1, b2, b3 = st.columns(3) 
            b1.button("Refresh All Prices", type="primary", use_container_width=True,
                      on_click=refresh_all_prices, args=(active_group_id,),
                      disabled=(active_group.get('status') == 'closed')
            )
            b2.button(f"⚠️ Close All Positions ({active_group['name']})", use_container_width=True,
                      on_click=close_all_positions, args=(active_group_id,),
                      help="This will mark all active legs as 'closed' and move the strategy to the 'Closed' list.",
                      disabled=(active_group.get('status') == 'closed')
            )
            with b3:
                st.session_state.auto_refresh = st.checkbox("Auto-Refresh Prices (15s)", value=st.session_state.auto_refresh, key="auto_refresh_toggle")

            
            st.markdown("---")

            st.header("Positions")
            st.info("Use the 'Actions' expander on any leg to update or exit it. Closed legs are greyed out.")

            if not processed_legs:
                st.info("No positions added yet. Add legs manually from the 'Option Chain' tab.")
            else:
                for leg in processed_legs:
                    is_closed = (leg.get('status') == 'closed')
                    style = "opacity: 0.5;" if is_closed else "" 
                    
                    with st.container():
                        st.markdown(f'<div style="{style}">', unsafe_allow_html=True)
                        with st.container(border=True):
                            col1, col2, col3 = st.columns([4, 2, 2])
                            
                            with col1:
                                side_text = "BUY" if leg.get('side') == 'long' else "SELL"
                                side_color = "green" if leg.get('side') == 'long' else "red"
                                price_text = f"@{leg.get('entry_premium', 0.0):.2f}"
                                if is_closed:
                                    price_text += f" → {leg.get('exit_price', 0.0):.2f}"

                                st.markdown(f"**<span style='color:{side_color};'>{side_text}</span> {leg.get('lots', 0)}x** {price_text}", unsafe_allow_html=True)
                                st.markdown(f"#### {leg.get('strike', 'N/A')} {leg.get('type', 'N/A')}")
                                st.caption(f"Tag: {leg.get('strategy', 'N/A')}")
                            with col2:
                                ltp_val = leg.get('current_ltp', 0.0) if not is_closed else leg.get('exit_price', 0.0)
                                st.metric(label="LTP" if not is_closed else "Exit Price", value=f"{ltp_val:.2f}")
                            with col3:
                                pnl_val = leg.get('pnl', 0.0)
                                pnl_color = "normal" if pnl_val >= 0 else "inverse"
                                label = "PnL" if not is_closed else "Realised PnL"
                                st.metric(label=label, value=f"₹{pnl_val:,.0f}", delta_color=pnl_color)

                            if not is_closed:
                                with st.expander("Actions"):
                                    form_key = f"form_{leg['id']}"
                                    with st.form(key=form_key):
                                        c1, c2, c3 = st.columns(3)
                                        with c1:
                                            new_lots = st.number_input("Lots", value=int(leg['lots']), min_value=1, step=1, key=f"lots_{leg['id']}")
                                        with c2:
                                            new_entry = st.number_input("Entry", value=float(leg['entry_premium']), format="%.2f", step=0.05, key=f"entry_{leg['id']}")
                                        with c3:
                                            new_tag = st.text_input("Tag", value=leg['strategy'], key=f"tag_{leg['id']}")
                                        
                                        s1, s2 = st.columns(2)
                                        with s1:
                                            st.form_submit_button(
                                                "Update Leg", 
                                                use_container_width=True,
                                                on_click=update_leg_details,
                                                args=(active_group_id, leg['id'], new_lots, new_entry, new_tag)
                                            )
                                        with s2:
                                            st.form_submit_button(
                                                "Exit Leg", 
                                                use_container_width=True, 
                                                type="primary",
                                                on_click=exit_leg,
                                                args=(active_group_id, leg['id'])
                                            )
                        st.markdown('</div>', unsafe_allow_html=True)


            st.markdown("---")
            
            st.header("🔥 Adjustment Signal & Firefighting")
            
            if active_group.get('status') == 'closed':
                st.info("This strategy is closed. Firefighting is disabled.")
            else:
                active_group['buffer'] = st.number_input(
                    "Firefighting Buffer (pts)", 
                    value=active_group.get('buffer', 100),
                    step=10,
                    key=f"buffer_{active_group_id}"
                )

                avg_strike = stats['avg_strike']
                buffer = active_group['buffer']
                step = INDEX_MAP[active_group['instrument']]["step"]
                total_pnl = stats['total_pnl'] 

                if avg_strike == 0:
                    st.info("Add an active base leg (straddle/strangle) to enable firefighting signals.")
                else:
                    trigger_up = avg_strike + buffer
                    trigger_down = avg_strike - buffer
                    
                    st.metric(
                        label=f"Avg. Short Strike: {avg_strike:,.0f} | Buffer: {buffer} pts",
                        value=f"Live Spot: {spot:,.2f}",
                        delta=f"Safe Range: {trigger_down:,.0f} - {trigger_up:,.0f}"
                    )
                    st.markdown("---")

                    if spot > trigger_up:
                        st.error(f"**ADJUST!** Spot ({spot:,.2f}) > Upper Trigger ({trigger_up:,.0f}). Firefight UP!")
                        st.subheader("Recommended Action")
                        if total_pnl >= 0:
                            st.info("Position is in profit. Shifting is recommended.")
                            st.button(f"Shift Base to ATM @ {atm_strike}", on_click=firefight_shift_base, args=(active_group_id, atm_strike), use_container_width=True, type="primary")
                        else:
                            st.warning("Position is in loss. Averaging is recommended.")
                            s2_strike = s2_from_s1_and_spot(avg_strike, spot, step)
                            st.button(f"Averaging (S2): Sell Straddle @ {s2_strike}", on_click=firefight_average, args=(active_group_id, s2_strike), use_container_width=True, type="primary")
                        
                        st.markdown("---")
                        st.subheader("All Firefighting Options")
                        s2_strike = s2_from_s1_and_spot(avg_strike, spot, step)
                        ref_strike_down = round((avg_strike - buffer) / step) * step
                        ext_strike_up = round((avg_strike + buffer + buffer) / step) * step
                        c1, c2, c3 = st.columns([1, 2, 1]); c1.markdown("**Technique**"); c2.markdown("**Action**"); c3.markdown("**Execute**")
                        c1, c2, c3 = st.columns([1, 2, 1]); c1.write("Averaging (S2)"); c2.write(f"Sell Straddle @ {s2_strike}"); c3.button("Execute", key="ff_avg_table_up", on_click=firefight_average, args=(active_group_id, s2_strike), use_container_width=True)
                        c1, c2, c3 = st.columns([1, 2, 1]); c1.write("Adjust (Reference)"); c2.write(f"Sell PE @ {ref_strike_down:.0f}"); c3.button("Execute", key="ff_ref_table_up", on_click=firefight_add_reference_trade, args=(active_group_id, ref_strike_down, "PE"), use_container_width=True)
                        c1, c2, c3 = st.columns([1, 2, 1]); c1.write("Extend Range"); c2.write(f"Sell CE @ {ext_strike_up:.0f}"); c3.button("Execute", key="ff_ext_table_up", on_click=firefight_true_extension, args=(active_group_id, ext_strike_up, "CE"), use_container_width=True)

                    elif spot < trigger_down:
                        st.error(f"**ADJUST!** Spot ({spot:,.2f}) < Lower Trigger ({trigger_down:,.0f}). Firefight DOWN!")
                        st.subheader("Recommended Action")
                        if total_pnl >= 0:
                            st.info("Position is in profit. Shifting is recommended.")
                            st.button(f"Shift Base to ATM @ {atm_strike}", on_click=firefight_shift_base, args=(active_group_id, atm_strike), use_container_width=True, type="primary")
                        else:
                            st.warning("Position is in loss. Averaging is recommended.")
                            s2_strike = s2_from_s1_and_spot(avg_strike, spot, step)
                            st.button(f"Averaging (S2): Sell Straddle @ {s2_strike}", on_click=firefight_average, args=(active_group_id, s2_strike), use_container_width=True, type="primary")
                        
                        st.markdown("---")
                        st.subheader("All Firefighting Options")
                        s2_strike = s2_from_s1_and_spot(avg_strike, spot, step)
                        ref_strike_up = round((avg_strike + buffer) / step) * step
                        ext_strike_down = round((avg_strike - buffer - buffer) / step) * step
                        c1, c2, c3 = st.columns([1, 2, 1]); c1.markdown("**Technique**"); c2.markdown("**Action**"); c3.markdown("**Execute**")
                        c1, c2, c3 = st.columns([1, 2, 1]); c1.write("Averaging (S2)"); c2.write(f"Sell Straddle @ {s2_strike}"); c3.button("Execute", key="ff_avg_table_down", on_click=firefight_average, args=(active_group_id, s2_strike), use_container_width=True)
                        c1, c2, c3 = st.columns([1, 2, 1]); c1.write("Adjust (Reference)"); c2.write(f"Sell CE @ {ref_strike_up:.0f}"); c3.button("Execute", key="ff_ref_table_down", on_click=firefight_add_reference_trade, args=(active_group_id, ref_strike_up, "CE"), use_container_width=True)
                        c1, c2, c3 = st.columns([1, 2, 1]); c1.write("Extend Range"); c2.write(f"Sell PE @ {ext_strike_down:.0f}"); c3.button("Execute", key="ff_ext_table_down", on_click=firefight_true_extension, args=(active_group_id, ext_strike_down, "PE"), use_container_width=True)
                    
                    else:
                        st.success(f"IN SAFE ZONE: Spot ({spot:,.2f}) is within range ({trigger_down:,.0f} - {trigger_up:,.0f}). Monitoring...")
                
                st.markdown("---")
                
                # --- Rebuilt Weekly Protection Tool ---
                st.header("🛡️ Add Weekly Protection (PR Sundar Method)")
                
                instrument_df = st.session_state.instrument_list
                today = date.today()
                
                active_instrument = active_group['instrument']
                all_expiries = []
                if instrument_df is not None:
                    all_expiries = instrument_df[
                        (instrument_df['name'] == active_instrument) & 
                        (instrument_df['instrumenttype'] == 'OPTIDX') &
                        (instrument_df['expiry'] >= today)
                    ]['expiry'].unique()
                
                weekly_expiries = [exp for exp in all_expiries if (exp > today) and (exp <= today + timedelta(days=10))]
                
                if not weekly_expiries:
                    st.warning(f"No near-term weekly expiries found for {active_instrument}.")
                else:
                    selected_weekly_expiry = st.selectbox("Select Weekly Expiry", options=sorted(weekly_expiries))
                    
                    base_short_legs = [l for l in processed_legs if l['side'] == 'short' and l['strategy'].startswith('base_') and l['status'] == 'active']
                    total_premium_points = 0
                    call_strike = None
                    put_strike = None
                    
                    if base_short_legs:
                        total_premium_points = sum(l['entry_premium'] for l in base_short_legs)
                        
                        ce_legs = [l['strike'] for l in base_short_legs if l['type'] == 'CE']
                        pe_legs = [l['strike'] for l in base_short_legs if l['type'] == 'PE']
                        
                        if ce_legs: call_strike = max(ce_legs)
                        if pe_legs: put_strike = min(pe_legs)
                        
                        if call_strike and not put_strike: put_strike = call_strike
                        if put_strike and not call_strike: call_strike = put_strike

                    if call_strike is None or put_strike is None:
                        st.warning("Add active 'base_straddle' or 'base_strangle' legs and **update their 'Entry' price** to calculate break-evens.")
                    else:
                        step = INDEX_MAP[active_instrument]["step"]
                        
                        put_be_strike = put_strike - total_premium_points
                        call_be_strike = call_strike + total_premium_points
                        
                        put_hedge_strike = round(put_be_strike / step) * step
                        call_hedge_strike = round(call_be_strike / step) * step
                        
                        st.info(f"Total Active Premium: **{total_premium_points:,.2f} pts**\n\n"
                                f"Call Break-Even: {call_strike:,.0f} + {total_premium_points:,.2f} = **{call_be_strike:,.2f}**\n\n"
                                f"Put Break-Even: {put_strike:,.0f} - {total_premium_points:,.2f} = **{put_be_strike:,.2f}**")
                        
                        col1, col2 = st.columns(2)
                        with col1:
                            st.button(f"Buy {put_hedge_strike} PE (Weekly)", 
                                      on_click=add_weekly_hedge, 
                                      args=(active_group_id, active_instrument, selected_weekly_expiry, put_hedge_strike, "PE"),
                                      use_container_width=True
                            )
                        with col2:
                            st.button(f"Buy {call_hedge_strike} CE (Weekly)", 
                                      on_click=add_weekly_hedge, 
                                      args=(active_group_id, active_instrument, selected_weekly_expiry, call_hedge_strike, "CE"),
                                      use_container_width=True
                            )
        
        # --- TAB 2: Option Chain (Manual Builder) ---
        with tab_chain:
            st.header("Market Selector")
            instrument_df = st.session_state.instrument_list
            today = date.today()
            
            sc1, sc2 = st.columns(2)
            with sc1:
                selected_instrument_for_chain = st.selectbox(
                    "Select Instrument (for Chain)", 
                    ["NIFTY", "BANKNIFTY", "FINNIFTY"],
                    key="selected_instrument_chain",
                )
            
            instrument_options = pd.DataFrame()
            if instrument_df is not None:
                instrument_options = instrument_df[
                    (instrument_df['name'] == selected_instrument_for_chain) & 
                    (instrument_df['instrumenttype'] == 'OPTIDX') &
                    (instrument_df['expiry'] >= today)
                ]
            
            if instrument_options.empty:
                st.warning(f"No active options found for {selected_instrument_for_chain}.")
                st.session_state.current_chain = pd.DataFrame() # <-- ADD THIS LINE
            else:
                with sc2:
                    unique_expiries = sorted(instrument_options['expiry'].unique())
                    selected_expiry_for_chain = st.selectbox(
                        "Select Expiry Date",
                        options=unique_expiries,
                        key="selected_expiry_chain"
                    )
                
                chain_df = instrument_options[
                    instrument_options['expiry'] == selected_expiry_for_chain
                ].copy() 
                
                chain_df['lotsize'] = chain_df['name'].map({k: v['lot_size'] for k, v in INDEX_MAP.items()})

                calls_df = chain_df[chain_df['symbol'].str.endswith('CE')][['strike', 'symbol', 'token', 'exch_seg', 'lotsize']]
                puts_df = chain_df[chain_df['symbol'].str.endswith('PE')][['strike', 'symbol', 'token', 'exch_seg', 'lotsize']]
                
                full_chain = pd.merge(
                    calls_df, 
                    puts_df, 
                    on='strike', 
                    suffixes=('_CE', '_PE')
                ).sort_values(by='strike').reset_index(drop=True)
                
                full_chain['lotsize_CE'] = full_chain['lotsize_CE'].fillna(full_chain['lotsize_PE'])
                full_chain['lotsize_PE'] = full_chain['lotsize_PE'].fillna(full_chain['lotsize_CE'])
                
                st.session_state.current_chain = full_chain
            
            st.markdown("---")

            if active_group.get('status') == 'closed':
                st.warning(f"This strategy '{active_group['name']}' is closed. You cannot add new legs. Please create a new strategy.")
            
            if active_group['instrument'] != selected_instrument_for_chain:
                st.warning(f"This chain is for {selected_instrument_for_chain}, but your active strategy '{active_group['name']}' is for {active_group['instrument']}. New legs will be added to '{active_group['name']}'.", icon="ℹ️")
            
            st.header(f"Manual Leg Builder: {selected_instrument_for_chain} ({selected_expiry_for_chain})")

            chain_spot_price = st.session_state.all_index_prices.get(selected_instrument_for_chain, 0.0)
            chain_atm_strike = 0.0
            
            if chain_spot_price > 0:
                step = INDEX_MAP[selected_instrument_for_chain]["step"]
                chain_atm_strike = round(chain_spot_price / step) * step
                st.info(f"**Live Spot ({selected_instrument_for_chain}):** {chain_spot_price:,.2f} | **Nearest ATM Strike:** {chain_atm_strike:,.0f}")
            else:
                st.info("Fetching spot price... (Click 'Refresh' in sidebar if needed)")
                if st.button("Fetch Spot Price"):
                    refresh_all_index_prices()

            
            st.markdown("---")
            filtered_chain_df = st.session_state.current_chain
            
            if not filtered_chain_df.empty:
                f1, f2 = st.columns([1, 3])
                with f1:
                    show_atm_only = st.checkbox(f"Show Near ATM (±{10 * INDEX_MAP[selected_instrument_for_chain]['step']} pts)", value=True)
                with f2:
                    all_strikes = filtered_chain_df['strike'].tolist()
                    min_strike, max_strike = all_strikes[0], all_strikes[-1]
                    if len(all_strikes) > 1:
                        strike_range = st.select_slider("Filter Strike Range", options=all_strikes, value=(min_strike, max_strike))
                    else:
                        strike_range = (min_strike, max_strike)
                
                filtered_chain_df = filtered_chain_df[
                    (filtered_chain_df['strike'] >= strike_range[0]) & 
                    (filtered_chain_df['strike'] <= strike_range[1])
                ]
                
                if show_atm_only and chain_atm_strike > 0:
                    atm_range = 10 * INDEX_MAP[selected_instrument_for_chain]["step"]
                    filtered_chain_df = filtered_chain_df[
                        (filtered_chain_df['strike'] >= chain_atm_strike - atm_range) & 
                        (filtered_chain_df['strike'] <= chain_atm_strike + atm_range) 
                    ]
            st.markdown("---")
            
            chain_container = st.container(height=600)
            
            with chain_container:
                c1, c2, c3, c4, c5, c6, c7 = st.columns([1, 1, 2, 1, 2, 1, 1])
                c1.markdown("**Sell CALL**"); c2.markdown("**Buy CALL**"); c3.markdown("**CALL Symbol**"); c4.markdown("**Strike**"); c5.markdown("**PUT Symbol**"); c6.markdown("**Buy PUT**"); c7.markdown("**Sell PUT**")
                
                is_disabled = (active_group.get('status') == 'closed') # Disable buttons if strategy is closed

                for row in filtered_chain_df.itertuples():
                    is_atm = (row.strike == chain_atm_strike)
                    c1, c2, c3, c4, c5, c6, c7 = st.columns([1, 1, 2, 1, 2, 1, 1])
                    
                    if is_atm: c4.success(f"**{row.strike}**")
                    else: c4.write(row.strike)

                    c1.button("S", key=f"sell_ce_{row.strike}", help=f"Sell {row.symbol_CE}", on_click=add_leg_to_group, args=(active_group_id, "short", "CE", row.strike, row.symbol_CE, row.token_CE, row.exch_seg_CE, row.lotsize_CE), disabled=is_disabled)
                    c2.button("B", key=f"buy_ce_{row.strike}", help=f"Buy {row.symbol_CE}", on_click=add_leg_to_group, args=(active_group_id, "long", "CE", row.strike, row.symbol_CE, row.token_CE, row.exch_seg_CE, row.lotsize_CE), disabled=is_disabled)
                    c3.write(row.symbol_CE)
                    c5.write(row.symbol_PE)
                    c6.button("B", key=f"buy_pe_{row.strike}", help=f"Buy {row.symbol_PE}", on_click=add_leg_to_group, args=(active_group_id, "long", "PE", row.strike, row.symbol_PE, row.token_PE, row.exch_seg_PE, row.lotsize_PE), disabled=is_disabled)
                    c7.button("S", key=f"sell_pe_{row.strike}", help=f"Sell {row.symbol_PE}", on_click=add_leg_to_group, args=(active_group_id, "short", "PE", row.strike, row.symbol_PE, row.token_PE, row.exch_seg_PE, row.lotsize_PE), disabled=is_disabled)

        # --- TAB 3: Trade History ---
        with tab_history:
            st.header("📓 Trade History Log")
            st.info("This shows the most recent actions at the top.")
            
            # --- MODIFIED Button ---
            if st.button("Clear History", on_click=clear_trade_history):
                pass # Logic is now in the callback function
            
            if not st.session_state.trade_history:
                st.warning("No trade actions have been recorded yet.")
            else:
                log_container = st.container(height=500)
                log_text = "\n\n".join([f"- {entry}" for entry in st.session_state.trade_history])
                log_container.markdown(log_text)

else:
    # --- LOGGED OUT STATE ---
    st.warning("Please log in to connect to Angel One and start the app.")
    if st.button("Login to Angel One"):
        with st.spinner("Logging in, please wait..."):
            login_to_angel()

# --- NEW: Auto-Refresh Logic ---
if st.session_state.get("auto_refresh", False) and st.session_state.get("active_group_id") is not None:
    if st.session_state.get('active_group_id') in st.session_state.get('strategy_groups', {}):
        active_group = st.session_state.strategy_groups[st.session_state.active_group_id]
        if active_group.get('status') == 'active':
            time.sleep(15)
            refresh_all_prices(st.session_state.active_group_id)
            st.rerun()