import requests
import time
import pandas as pd
import getpass
from math import ceil
import streamlit as st
import os

SWIGGY_URL = 'https://www.swiggy.com'
SWIGGY_LOGIN_URL = SWIGGY_URL + '/dapi/auth/signin-with-check'
SWIGGY_ORDER_URL = SWIGGY_URL + '/dapi/order/all'
SWIGGY_SEND_OTP_URL = SWIGGY_URL + '/dapi/auth/sms-otp'
SWIGGY_VERIFY_OTP_URL = SWIGGY_URL + '/dapi/auth/otp-verify'
SWIGGY_API_CALL_INTERVAL = 1.5  # interval between API calls (in seconds)

def fetch_swiggy_orders():
    """
    Fetch all Swiggy orders and return DataFrames.
    
    Returns:
    --------
    tuple
        (orders_df, items_df) - DataFrames containing the orders and items
    """
    session = requests.Session()
    session.headers = {
        "user-agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/115.0.0.0 Safari/537.36"
        )
    }
    
    # Step 1: Establish connection and get CSRF token
    with st.spinner("Establishing connection with Swiggy..."):
        establish_connection = session.get(SWIGGY_URL)
        
        # Parse CSRF token from response
        csrf_token = establish_connection.text.split("csrfToken")[1].split("=")[1].split(";")[0][2:-1]
        
        # Get SW cookie
        sw_cookie = establish_connection.cookies.get_dict().get('__SW')
        
        if not csrf_token or not sw_cookie:
            st.error("Unable to establish connection with Swiggy. Login failed")
            return None, None
    
    # Step 2: Request OTP
    username = st.text_input("Enter your registered mobile number:")
    
    if not username:
        return None, None
    
    request_otp = st.button("Request OTP")
    
    if not request_otp:
        return None, None
    
    with st.spinner(f"Requesting OTP for {username}..."):
        otp_response = session.post(
            SWIGGY_SEND_OTP_URL, 
            headers={
                'content-type': 'application/json',
                'Cookie': f'__SW={sw_cookie}',
            },
            json={"mobile": username, '_csrf': csrf_token}
        )
        
        if otp_response.text == "Invalid Request":
            st.error("Error from Swiggy API while sending OTP")
            return None, None
    
    # Step 3: Refresh connection for new CSRF token
    with st.spinner("Refreshing connection..."):
        re_establish_connection = session.get(SWIGGY_URL)
        csrf_token = re_establish_connection.text.split("csrfToken")[1].split("=")[1].split(";")[0][2:-1]
    
    # Step 4: Verify OTP
    otp_input = st.text_input("Enter the OTP sent to your mobile:", type="password")
    
    verify_otp = st.button("Verify OTP")
    
    if not verify_otp or not otp_input:
        return None, None
    
    with st.spinner("Verifying OTP..."):
        otp_verify_response = session.post(
            SWIGGY_VERIFY_OTP_URL, 
            headers={'content-type': 'application/json'},
            json={"otp": otp_input, '_csrf': csrf_token}
        )
        
        if otp_verify_response.text == "Invalid Request" or otp_verify_response.status_code != 200:
            st.error("Invalid OTP or login failed")
            return None, None
    
    # Step 5: Fetch all orders with pagination handling
    st.success("Logged in successfully!")
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # Initialize lists to store order details and items
    all_orders = []
    all_items = []
    
    # Make initial request to get total order count
    with st.spinner("Fetching orders..."):
        response = session.get(SWIGGY_ORDER_URL)
        
        if not response.json().get('data', None):
            st.error("Unable to fetch orders")
            return None, None
        
        # Get orders
        orders = response.json().get('data').get('orders', [])
        
        # Check if user has zero orders
        if isinstance(orders, list) and len(orders) == 0:
            st.info("You have not placed any order, no data to fetch :)")
            return pd.DataFrame(), pd.DataFrame()
        
        # Process initial batch of orders
        process_orders_batch(orders, all_orders, all_items)
        
        # Get total order count and calculate pagination
        count = response.json().get('data').get('total_orders', 0)
        pages = ceil(count/10)
        
        status_text.text(f"Found {count} orders. Fetching all pages...")
        
        # Initialize offset_id for pagination
        offset_id = orders[-1]['order_id']
        
        # Fetch remaining pages (starting from page 2)
        for i in range(1, pages):
            progress = (i / pages)
            progress_bar.progress(progress)
            status_text.text(f"Fetching page {i+1}/{pages}...")
            
            try:
                orders = fetch_orders_page(session, offset_id)
            except Exception as e:
                st.error(f"Error fetching page {i+1}: {e}")
                break
                
            if len(orders) == 0:
                status_text.text("No more orders to fetch.")
                break
                
            # Process this batch of orders
            process_orders_batch(orders, all_orders, all_items)
            
            # Update offset_id for next page
            offset_id = orders[-1]['order_id']
            
            # Be nice to Swiggy's servers
            time.sleep(SWIGGY_API_CALL_INTERVAL)
    
    progress_bar.progress(1.0)
    status_text.text("All orders fetched successfully!")
    
    # Create DataFrames
    orders_df = pd.DataFrame(all_orders, columns=[
        'order_id', 'order_total', 'restaurant_name', 'order_time', 'rain_mode', 'on_time'
    ])
    
    items_df = pd.DataFrame(all_items, columns=[
        'order_id', 'name', 'is_veg'
    ])
    
    return orders_df, items_df

def fetch_orders_page(session, offset_id):
    """Fetch a single page of orders using the offset_id"""
    try:
        response = session.get(SWIGGY_ORDER_URL + '?order_id=' + str(offset_id))
        fin = response.json().get('data').get('orders', [])
        if fin == []:
            st.warning("No more orders to fetch.")
            st.text(response.text)
        return fin
    except requests.exceptions.ConnectionError:
        # Retry once on connection error
        time.sleep(2)
        response = session.get(SWIGGY_ORDER_URL + '?order_id=' + str(offset_id))
        return response.json().get('data').get('orders', [])
    except Exception as e:
        raise Exception(f"Error while fetching orders: {e}")

def process_orders_batch(orders, all_orders, all_items):
    """Process a batch of orders and extract relevant information"""
    # Filter delivered orders
    delivered_orders = list(filter(lambda i: i.get('order_status', '') == 'Delivered', orders))
    
    for order in delivered_orders:
        order_id = order.get('order_id')
        order_total = order.get('order_total')
        restaurant_name = order.get('restaurant_name')
        order_time = order.get('order_time')
        rain_mode = order.get('rain_mode', False)
        on_time = order.get('on_time', True)
        
        all_orders.append([order_id, order_total, restaurant_name, order_time, rain_mode, on_time])
        
        if order.get('order_items'):
            for item in order.get('order_items'):
                is_veg = item.get('is_veg')
                name = item.get('name')
                all_items.append([order_id, name, is_veg])

# Streamlit app
st.title("Swiggy Order History Downloader")
st.write("This app helps you download your complete Swiggy order history as CSV files.")

with st.expander("How it works"):
    st.markdown("""
    1. Enter your registered mobile number
    2. Request and enter the OTP sent to your phone
    3. The app will fetch all your Swiggy orders
    4. Download your order history as CSV files
    
    **Note:** Your login information is not stored anywhere.
    """)

# Main app flow
if 'orders_df' not in st.session_state or 'items_df' not in st.session_state:
    st.session_state.orders_df = None
    st.session_state.items_df = None

if st.session_state.orders_df is None:
    orders_df, items_df = fetch_swiggy_orders()
    if orders_df is not None and items_df is not None:
        st.session_state.orders_df = orders_df
        st.session_state.items_df = items_df

# Display and download options if data is available
if st.session_state.orders_df is not None and not st.session_state.orders_df.empty:
    st.success("Data fetched successfully! You can now download your order history.")
    
    # Display sample data
    st.subheader("Sample of your order data")
    st.dataframe(st.session_state.orders_df.head())
    
    st.subheader("Sample of your ordered items")
    st.dataframe(st.session_state.items_df.head())
    
    # Download buttons
    col1, col2 = st.columns(2)
    
    with col1:
        csv_orders = st.session_state.orders_df.to_csv(index=False)
        st.download_button(
            label="Download Orders CSV",
            data=csv_orders,
            file_name="swiggy_orders.csv",
            mime="text/csv"
        )
    
    with col2:
        csv_items = st.session_state.items_df.to_csv(index=False)
        st.download_button(
            label="Download Items CSV",
            data=csv_items,
            file_name="swiggy_items.csv",
            mime="text/csv"
        )