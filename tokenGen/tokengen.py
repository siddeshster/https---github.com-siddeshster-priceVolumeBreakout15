from kiteconnect import KiteConnect

def tokengen():
    kite = KiteConnect(api_key="4v210llwkbl8uimi")
    data = kite.generate_session("44RB2RdGZVvKffLtOW7O8uTEcEXlRWam", api_secret="4kiy063fs1q543s86sj3vveqp2j8xobh")
    access_token = data["access_token"]

    print("Access Token:", access_token)

# https://kite.trade/connect/login?api_key=4v210llwkbl8uimi&v=3

# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    tokengen()

# See PyCharm help at https://www.jetbrains.com/help/pycharm/
