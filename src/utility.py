
import random
import logging

log = logging.getLogger(__name__)


def print_list(list_data, prepend=""):

    list_data_cp = list(list_data)

    for line in list_data_cp:
        log.info(prepend + str(line))


def get_randint(n):

    range_start = 10**(n-1)
    range_end = (10**n)-1
    return random.randint(range_start, range_end)
