def norm_pid(v):
    v = (v or "").strip()
    v = v.lstrip("0")
    return v or "0"

def norm_txt(v):
    return " ".join((v or "").upper().split())

# owner_key/owner_id are deliberately NOT here. Minting an owner id belongs to
# build-db.py, which has its own copy: the server must read parcel.owner_id
# instead of deriving one, because the ownership grouping folds several owner
# keys onto one owner row and the hash of that row's own name and address is then
# not its id. Deriving it here served every parcel page a KeyError.

def to_int(v):
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    try:
        return int(float((v or "").strip()))
    except (TypeError, ValueError, AttributeError):
        return 0

def to_float(v):
    """None when the field does not hold a number. R writes large values in
    scientific notation, so this never goes near int()."""
    try:
        return float((v or "").strip())
    except (TypeError, ValueError, AttributeError):
        return None

def is_true(v):
    return (v or "").strip().upper() in ("TRUE", "T", "1", "YES")

def is_false(v):
    return (v or "").strip().upper() in ("FALSE", "F", "0", "NO")

# ---------------------------------------------------------------------------
# facet bits
# ---------------------------------------------------------------------------
# One byte a parcel carries every boolean the filtered pages test, so a filter
# pass reads a bytearray instead of re-parsing four text fields per row.
F_OOS = 1     # is_owner_out_of_state

F_OCC = 2     # is_owner_occupied

F_FIN = 4     # is_financialized

F_MOM = 8     # is_mom_and_pop

F_SCOPE = 16  # parcel_in_scope(), cached

def fast_int(v):
    """to_int() with a fast path. Most roll numbers are plain digit strings;
    R writes the large ones in scientific notation, and those fall through."""
    return int(v) if v.isdigit() else to_int(v)

def fast_true(v):
    """is_true() for the roll's own spelling. The rolls write TRUE / FALSE
    literally, so the first character decides it."""
    return v[:1] in ("T", "t", "1", "Y", "y")
