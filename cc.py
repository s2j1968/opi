from configuration import configuration
from configuration import apiKey
from configuration import apiRedirectUri
from optionChain import OptionChain
from api import Api
from statistics import median
import sys


class Cc:

    def __init__(self, asset):
        self.asset = asset

    def find(self):
        asset = self.asset
        allowedDaysRange = configuration[asset]['days'] + configuration[asset]['daysSpread']

        api = Api(apiKey, apiRedirectUri)
        optionChain = OptionChain(api, asset, allowedDaysRange)

        chain = optionChain.get()
        # todo handle no chain found

        # get closest chain to days
        closestChain = min(chain, key=lambda x: abs(x['days'] - configuration[asset]['days']))

        dateTooClose = abs(closestChain['days'] - configuration[asset]['days']) < -configuration[asset]['daysSpread']
        dateTooFaar = abs(closestChain['days'] - configuration[asset]['days']) > configuration[asset]['daysSpread']

        # check if its within the spread
        if dateTooClose or dateTooFaar:
            return writingCcFailed('days range')

        #  get the best matching contract
        if configuration[asset]['rollCalendar']:
            # todo strike of last option, fail if it doesnt have one
            strikePrice = 0
        else:
            strikePrice = api.getATMPrice() + configuration[asset]['minGapToATM']

        contract = optionChain.getContractFromDateChain(strikePrice, closestChain['contracts'])

        # check minYield
        projectedPremium = median([contract['bid'], contract['ask']])

        if not configuration[asset]['rollCalendar'] and projectedPremium < configuration[asset]['minYield']:
            return writingCcFailed('minYield')

        return {
            'days': closestChain['days'],
            'contract': contract,
            'projectedPremium': projectedPremium
        }


def writeCcs():
    for asset in configuration:
        cc = Cc(asset).find()

        print('The bot wants to write the following contract:')
        print(cc)

        # writeCc(cc)


def writeCc(cc):
    # todo api
    return True

def writingCcFailed(message):
    # todo throw according to writeRequirementsNotMetAlert
    print(message)
    sys.exit(1)
