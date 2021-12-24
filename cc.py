from configuration import configuration, dbName, debugEverythingNeedsRolling
from optionChain import OptionChain
from statistics import median
from tinydb import TinyDB, Query
import datetime
import time


class Cc:

    def __init__(self, asset):
        self.asset = asset

    def findNew(self, api, existing, existingPremium):
        asset = self.asset

        optionChain = OptionChain(api, asset, configuration[asset]['days'], configuration[asset]['daysSpread'])

        chain = optionChain.get()
        # todo handle no chain found

        # get closest chain to days
        closestChain = min(chain, key=lambda x: abs(x['days'] - configuration[asset]['days']))

        # note: if the days or days - daysSpread in configuration amount to less than 3, date will always be too close
        # (with daysSpread only if it round down instead of up to get the best contract)
        dateTooClose = closestChain['days'] < 3 or abs(closestChain['days'] - configuration[asset]['days']) < -configuration[asset]['daysSpread']
        dateTooFar = abs(closestChain['days'] - configuration[asset]['days']) > configuration[asset]['daysSpread']

        minStrike = configuration[asset]['minStrike']
        atmPrice = api.getATMPrice(asset)
        strikePrice = atmPrice + configuration[asset]['minGapToATM']

        # check if its within the spread
        if dateTooClose or dateTooFar:
            return writingCcFailed('days range')

        if existing and existing['strike'] < atmPrice and configuration[asset]['rollWithoutDebit']:
            # ignore set minStrike
            # minStrike = existing['strike']

            # prevent paying debit with setting the minYield to the current price of existing
            minYield = existingPremium
        else:
            minYield = configuration[asset]['minYield']

            if minStrike > strikePrice:
                strikePrice = minStrike

        #  get the best matching contract
        contract = optionChain.getContractFromDateChain(strikePrice, closestChain['contracts'])

        if not contract:
            return writingCcFailed('minStrike')

        # check minYield
        projectedPremium = median([contract['bid'], contract['ask']])

        if projectedPremium < minYield:
            if configuration[asset]['rollWithoutDebit']:
                print('Failed to write contract for CREDIT with ATM price + minGapToATM ('+str(strikePrice)+'), now trying to get a lower strike ...')

                # we need to get a lower strike instead to not pay debit (min: existing strike, max: existing price + minGapToATM) and try again
                contract = optionChain.getContractFromDateChainByMinYield(existing['strike'], strikePrice, minYield, closestChain['contracts'])

                # edge case where this new contract fails: If even a calendar roll wouldn't result in a credit
                if not contract:
                    return writingCcFailed('minYield')

                projectedPremium = median([contract['bid'], contract['ask']])
            else:
                # the contract we want has not enough premium
                return writingCcFailed('minYield')

        return {
            'date': closestChain['date'],
            'days': closestChain['days'],
            'contract': contract,
            'projectedPremium': projectedPremium
        }

    def existing(self):
        db = TinyDB(dbName)
        ret = db.search(Query().stockSymbol == self.asset)
        db.close()

        return ret


def writeCcs(api):
    for asset in configuration:
        asset = asset.upper()
        cc = Cc(asset)

        try:
            existing = cc.existing()[0]
        except IndexError:
            existing = None

        if (existing and needsRolling(existing)) or not existing:
            if existing:
                existingPremium = api.getATMPrice(existing['optionSymbol'])
            else:
                existingPremium = 0

            new = cc.findNew(api, existing, existingPremium)

            print('The bot wants to write the following contract:')
            print(new)

            writeCc(api, asset, new, existing, existingPremium)
        else:
            print('Nothing to write ...')


def needsRolling(cc):
    if debugEverythingNeedsRolling:
        return True

    # needs rolling on date BEFORE expiration (if the market is closed, it will trigger ON expiration date)
    daysOffset = 1
    nowPlusOffset = (datetime.datetime.utcnow() + datetime.timedelta(days=daysOffset)).strftime('%Y-%m-%d')

    return nowPlusOffset >= cc['expiration']


def writeCc(api, asset, new, existing, existingPremium):
    if existing and existingPremium:
        # we use the db count here to prevent buying back more than we must if the amount in the configuration has changed
        amountToBuyBack = existing['count']

        orderId = api.writeNewContracts(
            existing['optionSymbol'],
            amountToBuyBack,
            existingPremium,
            new['contract']['symbol'],
            configuration[asset]['amountOfHundreds'],
            new['projectedPremium'],
        )
    else:
        orderId = api.writeNewContracts(
            None,
            0,
            0,
            new['contract']['symbol'],
            configuration[asset]['amountOfHundreds'],
            new['projectedPremium']
        )

    for x in range(12):
        # try to fill it for 12 * 5 seconds
        print('Waiting for order to be filled ...')

        checkedOrder = api.checkOrder(orderId)

        if checkedOrder['filled']:
            print('Order has been filled!')
            break

        time.sleep(5)

    if not checkedOrder['filled']:
        api.cancelOrder(orderId)

        # todo maybe lower the price instead of just failing
        return writingCcFailed('order cant be filled')

    soldOption = {
        'stockSymbol': asset,
        'optionSymbol': new['contract']['symbol'],
        'expiration': new['date'],
        'count': configuration[asset]['amountOfHundreds'],
        'strike': new['contract']['strike'],
        'receivedPremium': checkedOrder['price']
    }

    db = TinyDB(dbName)

    db.remove(Query().stockSymbol == asset)
    db.insert(soldOption)

    db.close()

    return soldOption


def writingCcFailed(message):
    # todo throw according to writeRequirementsNotMetAlert
    print(message)
    exit(1)
