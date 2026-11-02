"""LGW30 credential value-type unit tests -- redaction + non-serialization (task 5.2).

Supporting unit tests for the GW30 credential value types shipped in task 5.1
(``gateway_v2.dispatch.vector_credentials``):

- :class:`OrgCredential` redacts its secret in both ``repr`` and ``str`` -- the
  secret substring never appears in either rendering (R9.4, R9.5).
- :class:`OrgCredential` carries **no** credential serializer of its own: the
  type's own ``__dict__`` (and the type-only MRO) exposes no ``to_dict`` /
  ``to_json`` / ``__getstate__``. A credential is never put at rest.

  IMPORTANT: ``@dataclass(slots=True)`` auto-synthesizes ``__getstate__`` /
  ``__setstate__`` (which the 5.1 implementation strips), BUT ``object`` itself
  carries an inherited ``__getstate__`` on CPython 3.11+, so ``hasattr(x,
  '__getstate__')`` is True for every object. The at-rest invariant is therefore
  asserted against the CLASS ``__dict__`` and the MRO of the credential type
  itself -- excluding ``object`` -- NOT via ``hasattr``.
- :class:`CredentialResolution` ``ok`` is true only when a credential is present
  and false on every failure code (R10).

Test files are NOT under the import-linter layer contract, so importing from
``gateway_v2.dispatch`` here is allowed.

_Design: Testing Strategy (supporting unit tests)._
_Requirements: 9.4, 9.5._
"""

from __future__ import annotations

import dataclasses

from gateway_v2.dispatch.vector_credentials import CredentialResolution, OrgCredential
from gateway_v2.domain import posture

#: The GW30 credential failure codes -- every non-OK ``CredentialResolution`` code.
_FAILURE_CODES: tuple[str, ...] = (
    posture.CREDENTIAL_UNAVAILABLE,
    posture.CREDENTIAL_SCOPE_MISMATCH,
    posture.ORG_IDENTITY_INVALID,
    posture.CREDENTIAL_LEAK_WITHHELD,
)

#: The serializer names a credential must never expose on its own type.
_FORBIDDEN_SERIALIZERS: tuple[str, ...] = ("to_dict", "to_json", "__getstate__")


def _credential(secret: str = "super-secret-token-ABC123") -> OrgCredential:
    """A verified ``OrgCredential`` carrying a distinctive secret substring."""
    return OrgCredential(
        org_id="org-alpha",
        verified=True,
        _secret=secret,
        ref_id="cred-ref-0001",
    )


def test_repr_never_contains_the_secret_substring() -> None:
    """``repr(OrgCredential)`` redacts the secret -- the substring never appears (R9.4, R9.5)."""
    secret = "super-secret-token-ABC123"
    cred = _credential(secret)

    rendered = repr(cred)

    assert secret not in rendered, "repr leaked the secret substring"
    assert "secret=<redacted>" in rendered, "repr dropped the redaction marker"
    # The safe-to-log fields are still present for diagnostics.
    assert "org-alpha" in rendered
    assert "cred-ref-0001" in rendered


def test_str_never_contains_the_secret_substring() -> None:
    """``str(OrgCredential)`` redacts the secret -- the substring never appears (R9.4, R9.5)."""
    secret = "another-distinct-secret-XYZ789"
    cred = _credential(secret)

    rendered = str(cred)

    assert secret not in rendered, "str leaked the secret substring"
    assert "secret=<redacted>" in rendered, "str dropped the redaction marker"


def test_fstring_interpolation_never_leaks_the_secret() -> None:
    """``{cred}`` / ``{cred!r}`` route through the redacted str/repr (R9.4, R9.5)."""
    secret = "fstring-secret-QQQ000"
    cred = _credential(secret)

    assert secret not in f"{cred}"
    assert secret not in f"{cred!r}"
    assert secret not in "{}".format(cred)


def test_type_exposes_no_credential_serializer_in_its_own_dict() -> None:
    """``OrgCredential.__dict__`` carries no ``to_dict``/``to_json``/``__getstate__`` (R9.4, R9.5).

    Asserted against the CLASS ``__dict__`` -- not ``hasattr`` -- because
    ``object`` carries an inherited ``__getstate__`` on CPython 3.11+, so
    ``hasattr`` would be True for every object regardless of the credential type.
    """
    own = type(_credential()).__dict__
    for name in _FORBIDDEN_SERIALIZERS:
        assert name not in own, f"OrgCredential.__dict__ exposes a {name} serializer"


def test_type_mro_exposes_no_credential_serializer_excluding_object() -> None:
    """No ``to_dict``/``to_json``/``__getstate__`` in the credential type's own MRO (R9.4, R9.5).

    Scans every class in the MRO EXCEPT ``object`` -- ``object.__getstate__`` is
    an inherited default, not a per-type credential serializer, so it is
    excluded deliberately (the 5.1 implementer's note).
    """
    cred_type = type(_credential())
    for klass in cred_type.__mro__:
        if klass is object:
            continue
        for name in _FORBIDDEN_SERIALIZERS:
            assert name not in klass.__dict__, (
                f"{klass.__name__} in the OrgCredential MRO exposes a {name} serializer"
            )


def test_dataclass_synthesized_pickle_hooks_are_stripped() -> None:
    """The ``@dataclass(slots=True)`` ``__getstate__``/``__setstate__`` hooks are removed (R9.4)."""
    own = type(_credential()).__dict__
    assert "__getstate__" not in own, "synthesized __getstate__ was not stripped"
    assert "__setstate__" not in own, "synthesized __setstate__ was not stripped"


def test_resolution_ok_true_only_when_credential_present() -> None:
    """``CredentialResolution.ok`` is true exactly when a credential is present (R10)."""
    resolution = CredentialResolution(code=posture.VECTOR_OK, credential=_credential())

    assert resolution.ok is True
    assert resolution.credential is not None


def test_resolution_ok_false_on_every_failure_code() -> None:
    """``ok`` is false on every GW30 credential failure code (no credential present) (R10)."""
    for code in _FAILURE_CODES:
        resolution = CredentialResolution(code=code)
        assert resolution.ok is False, f"ok was True for failure code {code!r}"
        assert resolution.credential is None, f"credential present on failure code {code!r}"


def test_resolution_ok_false_when_credential_omitted_even_on_ok_code() -> None:
    """``ok`` keys off credential presence, not the code -- a code-only value is not ``ok`` (R10)."""
    resolution = CredentialResolution(code=posture.VECTOR_OK)

    assert resolution.ok is False
    assert resolution.credential is None


def test_value_types_are_frozen() -> None:
    """Both value types are frozen slotted dataclasses -- assignment raises (R9, R10)."""
    cred = _credential()
    resolution = CredentialResolution(code=posture.VECTOR_OK, credential=cred)

    try:
        cred.verified = False  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:  # pragma: no cover - defends the frozen invariant
        raise AssertionError("OrgCredential is not frozen")

    try:
        resolution.code = posture.CREDENTIAL_UNAVAILABLE  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:  # pragma: no cover - defends the frozen invariant
        raise AssertionError("CredentialResolution is not frozen")
