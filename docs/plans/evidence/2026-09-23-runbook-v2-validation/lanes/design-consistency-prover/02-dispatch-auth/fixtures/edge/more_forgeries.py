import copy
import pickle
from dataclasses import replace as rep
from functools import partial
from typing import cast

import gateway_v2.resolve.decision as rd
from gateway_v2.resolve.decision import DispatchAuthorization as DA


def f1(d):  return DA(d)                                    # aliased import
def f2(d):  return rd.DispatchAuthorization(d)              # module attribute
def f3(a, d): return rep(a, decision=d)                     # aliased dataclasses.replace
def f4(a, d): return type(a)(d)                             # type(x)(...)
def f5(a, d): return a.__class__(d)                         # x.__class__(...)
def f6(d):  return getattr(rd, "DispatchAuthorization")(d)  # getattr string
def f7(d):  ctor = DA; return ctor(d)                       # alias assignment
def f8(d):  return partial(DA, d)()                         # partial
def f9(o):  return cast(DA, o)                              # cast forgery
def f10(a): return copy.copy(a)                             # copy
def f11(a): return pickle.loads(pickle.dumps(a))            # pickle round trip
def f12(d): return rd.Decision(rd.Disposition.ALLOW, (), (), "v", (), ())  # Decision outside resolve/
def ok(a: DA) -> bool: return isinstance(a, DA)             # allowed: annotation + isinstance
