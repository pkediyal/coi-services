#!/usr/bin/env python

import unittest
from mock import Mock
from nose.plugins.attrib import attr

from pyon.core.exception import BadRequest, NotFound
from examples.bank.bank_service import BankService
from pyon.datastore.datastore import DataStore
from pyon.util.test_utils import switch_ref, pop_last_call

@attr('unit')
class TestBankService(unittest.TestCase):

    def setUp(self):
        self.bank_service = BankService()
        mock_clients = Mock()
        self.bank_service.clients = mock_clients
        self.mock_resource_registry = mock_clients.resource_registry

    def test_new_acct_existing_customer(self):
        mock_find = self.mock_resource_registry.find
        mock_result = Mock()
        mock_result._id = 'id_5'
        mock_find.return_value = [mock_result]
        mock_create = self.mock_resource_registry.create
        mock_create.return_value = ['id_2']
        with switch_ref('examples.bank.bank_service', 'IonObject') as mock_ionobj:
            account_id = self.bank_service.new_account('John')
            mock_find.assert_called_once_with([('type_', DataStore.EQUAL,
                'BankCustomer'), DataStore.AND, ('name', DataStore.EQUAL,
                    'John')])
            account_info = {'account_type' : 'Checking',
                    'owner' : 'id_5'}
            mock_ionobj.assert_called_once_with('BankAccount', account_info)
            mock_create.assert_called_once_with(mock_ionobj.return_value)
            self.assertEqual(account_id, 'id_2')

    def test_new_acct_new_customer(self):
        # Use mock side effect to simulate exception thrown
        mock_find = Mock()
        self.mock_resource_registry.find = mock_find
        mock_find.side_effect = NotFound('not there guys')
        # Use mock side effect to simulate two different return results
        results = [['acct_id_2'], ['cust_id_1']]
        def side_effect(*args, **kwargs):
            return results.pop()
        mock_create = self.mock_resource_registry.create
        mock_create.side_effect = side_effect
        with switch_ref('examples.bank.bank_service', 'IonObject') as mock_ionobj:
            account_id = self.bank_service.new_account('John')
            mock_find.assert_called_once_with([('type_', DataStore.EQUAL,
                'BankCustomer'), DataStore.AND, ('name', DataStore.EQUAL,
                    'John')])
            customer_info = {'name' : 'John'}
            account_info = {'account_type' : 'Checking',
                    'owner' : 'cust_id_1'}
            mock_ionobj.assert_called_with('BankAccount', account_info)
            pop_last_call(mock_ionobj)
            # asserting there's no more call before that...hence
            # called_once_with
            mock_ionobj.assert_called_once_with('BankCustomer', customer_info)
            self.assertEqual(mock_create.call_count, 2)
            mock_create.assert_called_with(mock_ionobj.return_value)
            pop_last_call(mock_create)
            mock_create.assert_called_once_with(mock_ionobj.return_value)
            self.assertEqual(account_id, 'acct_id_2')

    def test_deposit_not_found(self):
        mock_read = self.mock_resource_registry.read
        mock_read.return_value = None
        # exercise code
        with self.assertRaises(NotFound) as cm:
            self.bank_service.deposit('id_5')
        ex = cm.exception
        self.assertEqual(ex.message, 'Account id_5 does not exist')
        mock_read.assert_called_once_with('id_5')

    def test_deposit_okay(self):
        mock_read = self.mock_resource_registry.read
        mock_read.return_value = Mock()
        mock_read.return_value.cash_balance = 10
        mock_update = self.mock_resource_registry.update
        # exercise code in test 
        status = self.bank_service.deposit(account_id='id_5', amount=5)
        self.assertEqual(status, 'Balance after cash deposit: 15')
        mock_read.assert_called_once_with('id_5')
        assert mock_read.return_value.cash_balance == 10 + 5
        mock_update.assert_called_once_with(mock_read.return_value) 

    def test_withdraw_not_found(self):
        mock_read = self.mock_resource_registry.read
        mock_read.return_value = None
        # exercise code
        with self.assertRaises(NotFound) as cm:
            self.bank_service.withdraw('id_5')
        ex = cm.exception
        self.assertEqual(ex.message, 'Account id_5 does not exist')
        mock_read.assert_called_once_with('id_5')

    def test_withdraw_insufficient_funds(self):
        mock_read = self.mock_resource_registry.read
        mock_read.return_value = Mock()
        mock_read.return_value.cash_balance = 3
        mock_update = self.mock_resource_registry.update
        # exercise code in test
        with self.assertRaises(BadRequest) as cm:
            self.bank_service.withdraw('id_5', 5)
        # verify exception message
        ex = cm.exception
        self.assertEqual(ex.message, 'Insufficient funds')
        # No change in balance
        mock_read.assert_called_once_with('id_5')
        self.assertEqual(mock_read.return_value.cash_balance, 3)
        # mock update not called
        self.assertEqual(mock_update.call_count, 0)

    def test_withdraw_okay(self):
        mock_read = self.mock_resource_registry.read
        mock_read.return_value = Mock()
        mock_read.return_value.cash_balance = 10
        mock_update = self.mock_resource_registry.update
        # exercise code in test 
        status = self.bank_service.withdraw(account_id='id_5', amount=4)
        self.assertEqual(status, 'Balance after cash withdraw: 6')
        mock_read.assert_called_once_with('id_5')
        self.assertEqual(mock_read.return_value.cash_balance, 10 - 4)
        mock_update.assert_called_once_with(mock_read.return_value) 

    def test_get_balances_not_found(self):
        mock_read = self.mock_resource_registry.read
        mock_read.return_value = None
        # exercise code
        with self.assertRaises(NotFound) as cm:
            self.bank_service.withdraw('id_5')
        ex = cm.exception
        self.assertEqual(ex.message, 'Account id_5 does not exist')
        mock_read.assert_called_once_with('id_5')

    def test_get_balances_ok(self):
        mock_read = self.mock_resource_registry.read
        mock_read.return_value = Mock()
        mock_read.return_value.cash_balance = 10
        mock_read.return_value.bond_balance = 5
        # exercise code in test 
        cash_balance, bond_balance = self.bank_service.get_balances('id_5')
        self.assertEqual(cash_balance, 10)
        self.assertEqual(bond_balance, 5)
        mock_read.assert_called_once_with('id_5')

    def test_buy_bonds_not_found(self):
        pass

    def test_buy_bonds_insufficient_funds(self):
        pass

    def test_buy_bonds_ok(self):
        pass

    def test_sell_bonds_not_found(self):
        pass

    def test_sell_bonds_insufficient_funds(self):
        pass

    def test_sell_bonds_ok(self):
        pass

    def test_list_accounts_no_customers(self):
        pass

    def test_list_accounts_ok(self):
        pass
