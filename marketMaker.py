
from urllib3.exceptions import HTTPError as BaseHTTPError
import sys
import json
import requests
import random
import time
import datetime
import schedule
import os

'''
Filename: marketMaker.py
Description: Makes a market within 5% of the current bid/ask
Author: Brent Russell
Date: March 8, 2023
'''


# Globals
eth_balance = 10
usd_balance = 20000
orderbook_url = 'https://api.rhino.fi/bfx/v2/book/tETHUSD/R0'
current_open_orders = []
firstrun = 0


# Simple exception handler
def raise_ex(msg, terminate):
    """
    Prints a message and exits if terminate is passed
    Args:
        msg (string): The message to print
        terminate (bool): true or false to exit or not
    Returns:
        none
    """
    print(msg)
    if terminate:
        sys.exit(1)


def request_URL(url, rtype, headers='', payload=''):
    """
    Get the content from a URL and return it. Throw exception for common issues
    Args:
        url (string): The url we are hitting
        rtype (string): post or get
        headers (dict): Headers we want to send with the request
        payload (dict): Any payload to be sent in the body
    Returns:
        none
    """
    try:
        if rtype.lower() == 'post':
            r = requests.post(url, headers=headers, data=payload)
        else:
            r = requests.get(url, headers=headers)
    except requests.exceptions.Timeout:
        raise_ex("Connection to " + url + " Timmed out", True)
    except requests.exceptions.TooManyRedirects:
        raise_ex(url + " has redirected too many times", True)
    except requests.exceptions.HTTPError as e:
        raise_ex('HTTP Error ' + e.response.status_code, True)
    except (requests.exceptions.ConnectionError, requests.exceptions.RequestException):
        raise_ex("Connection Error, could not connect to " + url, True)
    else:
        return r


def get_orderbook():
    """
    Get the current orderbook, seperate it into bids and asks, and combine price amounts
    Args:
        none 
    Returns:
        none
    """
    try:
        data = request_URL(orderbook_url, 'get')
        book = json.loads(data.content)
        if len(book) > 0:
            current_orderbook = book
            '''
            We dont care how much is being sold in each individual order
            We  only care about the combined amount being sold at each price point
            Why don't we care? Because later we will need to calculate how much of an order has been filled
            We dont care how much of each individual order has been filled, only total amount filled
            So, here we combine the current asks items where the price value is the same and the same for the bids. 
            When doing this we will add up the amounts for each price point
            We also dont care about order numbers so we will ignore those
            '''
            unique_prices = []
            amount_sum = []
            for order in book:
                price = order[1]
                amount = order[2]
                # Check if price has already been seen
                if price in unique_prices:
                    # If so, add amount to existing sum for that price
                    index = unique_prices.index(price)
                    amount_sum[index] += amount
                else:
                    # If not, add price to unique list and amount to new sum
                    unique_prices.append(price)
                    amount_sum.append(amount)
            # Create new list with unique prices and summed amounts
            current_orderbook = [[unique_prices[i], amount_sum[i]] for i in range(len(unique_prices))]
            # Seperate Asks and bids in different lists
            current_bids = []
            current_asks = []
            for order in current_orderbook:
                if order[1] > 0:
                    current_asks.append(order)
                elif order[1] < 0:
                    current_bids.append(order)
            # Get the current max and min bid and ask
            orderbook = {
                "current_min_ask": min(current_asks, key=lambda x: x[0])[0],
                "current_min_bid": min(current_bids, key=lambda x: x[0])[0],
                "current_max_ask": max(current_asks, key=lambda x: x[0])[0],
                "current_max_bid": max(current_bids, key=lambda x: x[0])[0]
            }
            return orderbook
        else:
            raise_ex("Orderbook was zero length", False)
    except json.decoder.JSONDecodeError:
        raise_ex("get_orderbook method. Not a valid json response", False)
    except TypeError:
        raise_ex("get_orderbook method. TypeError. No records exist, is the Orderbook up?", False)
    except KeyError:
        raise_ex("get_orderbook method. Unable to find result key in json response", False)


def cancel_open_orders():
    """
    Cancels all open orders
    Args:
        none 
    Returns:
        none
    """
    global eth_balance
    global usd_balance
    global current_open_orders
    # Here we would cancel all open orders via the API
    # We cant for two reasons: 1) we are not actually submitting orders, 2) we are not tracking order numbers b/c we never actually submit
    # data = request_URL('https://api.stg.rhino.fi/v1/trading/w/cancelOrder', 'post','' ,'order_id')
    # Loop over orders adding back to the balances
    for open_order in current_open_orders:
        if open_order['type'] == 'bid':
            # add amount to USD
            usd_balance += open_order['amount'] * open_order['price']
            # print("Added to USD Balance when canceling bid:", open_order['amount'] * open_order['price'])
        elif open_order['type'] == 'ask':
            # add amount to ETH
            eth_balance += open_order['amount']
    current_open_orders = []  # Remove all current orders


def process_filled_orders(orderbook):
    """
    Look at the orderbook, find orders that have been filled, subtrack or add those orders from the balances
    Args:
        orderbook (list): The current orderbook 
    Returns:
        none
    """
    global current_open_orders
    global eth_balance
    global usd_balance
    print("current Max Bid is", orderbook['current_max_bid'])
    print("current min Ask is", orderbook['current_min_ask'])
    # Loop over the open orders and see how much we have sold or bought
    for open_order in current_open_orders:
        if open_order['type'] == 'bid':
            if open_order['price'] < orderbook['current_max_bid']:
                # Loop over all current bid orders and settle the amounts that were sold
                # For the bid to get this high, ALL of our previous ask orders below that amount would have to have been sold
                # Here we would normally just check the order history for filled amounts to be sure but we are going to assume it for now
                usd_balance += open_order['amount'] * open_order['price']
                # print("Sold", open_order['amount'] * open_order['price'])
                current_open_orders.remove(open_order)  # Now that it is settled, remove it from the list
        elif open_order['type'] == 'ask':
            if open_order['price'] > orderbook['current_max_bid']:
                # Loop over all current bid orders and settle the amounts that were bought
                eth_balance += open_order['amount']
                # print("Bought", open_order['amount'])
                current_open_orders.remove(open_order)  # Now that it is settled, remove it from the list


def place_order(type, price, amount):
    """
    Place and order
    Args:
        type (string): bid or ask
        price (float): The USD price point of ETH
        amount (float): the amount of ETH bidding or asking
    Returns:
        none
    """
    global eth_balance
    global usd_balance
    global current_open_orders
    if type == 'bid':
        usd_balance -= amount * price  # deduct this amount from the balance because the order is pending and is pledged to be sold/bought
    elif type == 'ask':
        eth_balance -= amount  # deduct this amount from the eth balance because the order is pending and is pledged to be sold/bought
    # Build the request and place the order here
    # order_result = requestURL(https://api.stg.rhino.fi/v1/trading/w/submitOrder)
    # print("Created an open", type, "order for", amount, "ETH at the USD price of ", price, "ETH Balance Now:", eth_balance, "USD Balance Now:", usd_balance)
    new_order = {
        "order_number": random.randint(10000000, 99999999),
        "type": type,
        "amount": amount,
        "price": price,
        "total_usd": price * amount
    }
    current_open_orders.append(new_order)


def make_orderbook():
    """
    Makes the orderbook calling all the neccesary methods to make and settle orders
    Args:
        none
    Returns:
        none
    """
    global firstrun
    global eth_balance
    global usd_balance

    orderbook = get_orderbook()
    firstrun += 1
    if firstrun > 1:
        process_filled_orders(orderbook)
        cancel_open_orders()  # Just cancel all orders and resubmit ones that are still within the 5% threshold. It makes it easier but could be improved upon

    # Create new orders staggared above and bellow the best ask/bid at the order_amount_offset
    eth_amount_for_use = eth_balance * random.uniform(0.16, 0.25)  # Dont use more than 16 to 25% of our eth bankroll
    avg_eth_order_amount = eth_amount_for_use / 5
    usd_amount_for_use = usd_balance * random.uniform(0.16, 0.25)  # Dont use more than 16 to 25% of our usd bankroll
    avg_usd_order_amount = usd_amount_for_use / 5

    ask_price = orderbook['current_max_ask'] * (1 - random.uniform(0.0001, 0.01))
    place_order('ask', ask_price, avg_eth_order_amount)
    ask_price = orderbook['current_max_ask'] * (1 - random.uniform(0.011, 0.02))
    place_order('ask', ask_price, avg_eth_order_amount)
    ask_price = orderbook['current_max_ask'] * (1 - random.uniform(0.021, 0.03))
    place_order('ask', ask_price, avg_eth_order_amount)
    ask_price = orderbook['current_max_ask'] * (1 - random.uniform(0.031, 0.04))
    place_order('ask', ask_price, avg_eth_order_amount)
    ask_price = orderbook['current_max_ask'] * (1 - random.uniform(0.041, 0.05))
    place_order('ask', ask_price, avg_eth_order_amount)

    amount = avg_usd_order_amount / orderbook['current_max_bid']
    place_order('bid', orderbook['current_max_bid'] * (1 - random.uniform(0.0001, 0.01)), amount)
    place_order('bid', orderbook['current_max_bid'] * (1 - random.uniform(0.011, 0.02)), amount)
    place_order('bid', orderbook['current_max_bid'] * (1 - random.uniform(0.021, 0.03)), amount)
    place_order('bid', orderbook['current_max_bid'] * (1 - random.uniform(0.031, 0.04)), amount)
    place_order('bid', orderbook['current_max_bid'] * (1 - random.uniform(0.041, 0.05)), amount)

    # place_order('bid', orderbook['current_max_bid'] * random.uniform(1.0001, 1.01), amount)
    # place_order('bid', orderbook['current_max_bid'] * random.uniform(1.011, 1.02), amount)
    # place_order('bid', orderbook['current_max_bid'] * random.uniform(1.021, 1.03), amount)
    # place_order('bid', orderbook['current_max_bid'] * random.uniform(1.031, 1.04), amount)
    # place_order('bid', orderbook['current_max_bid'] * random.uniform(1.041, 1.05), amount)


def task():
    """
    Sets up the methods that should be run on a schedule and prints out results on the screen
    Args:
        none
    Returns:
        none
    """
    global eth_balance
    global usd_balance
    global current_open_orders
    os.system("clear")
    make_orderbook()
    print("\n\nETH Balance:", eth_balance, "| USD Balance:", usd_balance)
    print("\n\n======= Open Orders =======")
    for element in current_open_orders:
        print(element['type'], "Price", element['price'], "Amount", element['amount'], "total_usd", element['total_usd'], )


# Schedule task
schedule.every(5).seconds.do(task)

while True:
    schedule.run_pending()  # Run pending schedules
    time.sleep(1)  # pause for execution, preventing memory/cpu overload
