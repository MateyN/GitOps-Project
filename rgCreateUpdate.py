#!/usr/bin/env python
#
# Copyright (c) 2023 by PROS, Inc.  All Rights Reserved.
# This software is the confidential and proprietary information of
# PROS, Inc. ("Confidential Information").
# You may not disclose such Confidential Information, and may only
# use such Confidential Information in accordance with the terms of
# the license agreement you entered into with PROS.
#
# @author nkalaydzhiev@pros.com
import os
import uuid
import sys
import logging   # decide on logging strategy
# from pprint import pprint as pp # printing nicely lists and dictionaries

# Azure libraries
from azure.core import exceptions
from azure.identity import DefaultAzureCredential
from azure.mgmt.resource.resources import ResourceManagementClient
from azure.mgmt.resource.resources.models import ResourceGroup
from azure.mgmt.authorization import AuthorizationManagementClient

# SVFB py - for now you need to do export PYTHONPATH="${PYTHONPATH}:<path-to-pros-azure/aks/py/lib>"
# for these imports to work. This needs to become part of the venv.
from merge import read_yaml_config as merge_conf

# ANSI escape sequence for terminal colors
_red = '\033[91m'
_yellow = '\033[93m'
_green = '\033[92m'
_cyan = '\033[36m'
_color_reset = '\033[0m'


# Azure specific print_item function
def print_azure_item(group):
    """Print an azure instance."""
    # print(f'{group}\n')
    print(f'\tName: {group.name}')
    print(f'\tId: {group.id}')
    print(f'\tLocation: {group.location}')
    print(f'\tTags: {group.tags}')


def get_realm_details(name, data):
    """
    This function loops trough list of dictionaries and looks for key named "name" if it matches the
    provided parameter name returns the full dictionary that has it. The idea is to go trough a data
    structure like this:

    {'id': '4455251f-653b-4577-b1b4-46b78221d3f8',
    'name': 'cd',
    'region': 'centralus'}

    and return it if name == "cd".
    data should be list of dictionaries.
    """

    ret_dict = {}
    for _item in data:
        if _item["name"] == name:
            _ret_dict = _item

    return _ret_dict


def role_assignment(rg_roles=[]):
    msg = """
    This function expects the following yaml structure:
    admin_groups:
      - name: <AD-Group-or-User-ID>
        roles:
          - "Contributor"
          - "PROS Lock Contributor"
          - "PROS Service Contributora"
      - name: <AD-Group-or-User-ID>
        roles:
           ....
    It should be configured in the config yaml passed to this script.
    """

    if len(rg_roles) == 0:
        return print(msg)

    for _role in rg_roles:
        _r = _role["roles"]
        _r_ad_group = _role["name"]

        # The multiple list() calls are specific Azure unpacking way of this type of object to get the role IDs
        # assigned to this resource group
        # https://github.com/Azure-Samples/compute-python-msi-vm#role-assignement-to-the-msi-credentials
        for role_name in _r:
            try:
                _role_ids[role_name] = list(
                    auth_client.role_definitions.list(
                        _rg_created.id,
                        filter=f"roleName eq '{role_name}'"))[0].id
            except IndexError:
                print(
                    f"{_red}IndexError: Please check if the role {role_name} exists in the selected subscription!{_color_reset}"
                )

            if "DEBUG" in os.environ:
                print("[DEBUG]: Role IDs:")
                print(_role_ids, sep="\n")
                print()

            try:
                auth_client.role_assignments.create(
                    _rg_created.id,
                    uuid.uuid4(),  # Each assignment should have a unique id ( Azure requirement )
                    {
                        "role_definition_id": _role_ids[role_name],
                        "principal_id": _r_ad_group,
                    },
                )
            except exceptions.ResourceExistsError as error:
                print(f"{_yellow}{error.message}{_color_reset}")


# For ADO usage we'll need to set a sligthly different login method
# https://learn.microsoft.com/en-us/azure/developer/python/sdk/authentication-local-development-service-principal?tabs=azure-portal#4---set-local-development-environment-variables
# DefaultAzureCredential looks for creds in the following order:
# ENV -> Managed Identity -> Azure CLI -> Azure PowerShell -> Interactive browser
_args = sys.argv

credentials = DefaultAzureCredential()

# Check if at least 2 arguments are passed (script and config file)
if not len(_args) >= 2:
    logging.error(f'{_args[0]} - ERROR: The script accepts a config yml file and optionally a realm name as arguments. Please provide at least the config file!')
    sys.exit(2)

# Read the rgConfig_<team>.yml and rgConfig_common.yml and store them in two objects
_config_file, _common_conf_file = merge_conf(_filename=_args[1])

_subs = _common_conf_file["subscriptions"]
tenant_id = _common_conf_file["tenant_id"]
realms = _config_file["realms"]

# Get the optional realm name from the third parameter
realm_name = _args[2] if len(_args) == 3 else None

# Check if the specified realm exists in the config file
if realm_name and realm_name not in realms:
    logging.error(f'{_red}ERROR: The realm "{realm_name}" does not exist in the config file!{_color_reset}')
    sys.exit(2)

# Filter the realms to process based on provided realm name, or process all if no realm name is provided
_realms_to_process = {realm_name: realms[realm_name]} if realm_name else realms

# iterate on all elements (rgs) from the yaml config
for _realm, _rgs in _realms_to_process.items():
    # dict for extracting the associated role_ids
    _role_ids = {}

    # set all the realm related data like subscription id, region
    _realm_sub_info = get_realm_details(_realm, _subs)
    subscription_id = _realm_sub_info["id"]

    # Construct the Azure  RM and auth clients
    rm_client = ResourceManagementClient(credentials, subscription_id)
    auth_client = AuthorizationManagementClient(credentials, subscription_id)

    # iterate trough all the RGs per realm
    print(f'\nRealm: {_cyan}{_realm}{_color_reset}')

    for _rg in _rgs:
        rg_name = _rg["name"]
        rg_roles = _rg["admin_groups"]
        # check if the team's config file has differenet region than the subscription
        region = _rg.get("region", _realm_sub_info["region"])
        rg_tags = _rg["tags"]

        print(f'Working on:\n rg_name: {rg_name}')

        _rg_exists = rm_client.resource_groups.check_existence(rg_name)

        if _rg_exists:
            print(f'[RG]: {rg_name} Already exist !\n Skipping creation...')

            _rg_created = rm_client.resource_groups.get(resource_group_name=rg_name)

            if _rg_created:
                print('[RG]: Updating tags...')

                _rg_created.tags = rg_tags
                rm_client.resource_groups.create_or_update(rg_name, _rg_created)

            print('[RG]: Updating role assignments...')
            role_assignment(rg_roles)

            print(f'[RG]: {_green}{rg_name} Updated !{_color_reset}')

        else:
            print(f'[RG]: Creating Resource Group {rg_name}...\n')

            _rg_created = rm_client.resource_groups.create_or_update(
                resource_group_name=rg_name,
                parameters=ResourceGroup(
                    location=region,
                    tags=rg_tags
                )
            )

            role_assignment(rg_roles)

            print(f'[RG]: {_green}{rg_name} Created !{_color_reset}\n')
            print_azure_item(_rg_created)
