from lm.coerce import is_false, is_true, to_float
from lm.schema import P

# ---------------------------------------------------------------------------
# the lookup scope
# ---------------------------------------------------------------------------
# The registry lookup never covers the whole roll. final_output_helper_functions.R
# builds the population every published figure is counted against with one
# dplyr::filter:
#
#   dplyr::filter(owner_data,
#                 ((is_financialized == TRUE) &
#                    (is_owner_occupied == FALSE)) |
#                   (property_units > 4),
#                 property_units != 0)
#
# Reproduced below, leg for leg. dplyr::filter keeps a row only when the whole
# condition evaluates TRUE, so an unreadable value anywhere in it drops the row
# rather than defaulting it in. That is why each leg tests for the literal value
# instead of for truthiness, and why an unparseable property_units is out.
#
# property_units > 4 is 5 units and over, which is the size the project means by
# a rental building. A parcel with no floor area on the roll is out either way,
# because units are themselves an estimate from floor area.
SCOPE_OCCUPIED = "occupied"

SCOPE_SIZE = "size"

SCOPE_NOSIZE = "nosize"

def parcel_in_scope(rec):
    units = to_float(rec[P["property_units"]])
    if units is None or units == 0:
        return False
    if units > 4:
        return True
    return (is_true(rec[P["is_financialized"]])
            and is_false(rec[P["is_owner_occupied"]]))

def scope_reason(rec):
    """Which coverage rule put this parcel outside the lookup. Owner-occupied
    first, because it is the fact a reader can check against the roll; the
    property_units != 0 leg second, because a zero there means the roll gave us
    no floor area to size the building from, which is not the same claim as the
    building being small."""
    if is_true(rec[P["is_owner_occupied"]]):
        return SCOPE_OCCUPIED
    units = to_float(rec[P["property_units"]])
    if units is None or units == 0:
        return SCOPE_NOSIZE
    return SCOPE_SIZE
