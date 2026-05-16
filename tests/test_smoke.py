from council.models import DecisionDossier


def test_decision_dossier_defaults() -> None:
    dossier = DecisionDossier(decision_question="Test?")
    assert dossier.decision_question == "Test?"
    assert dossier.run_id
    assert dossier.timestamp
