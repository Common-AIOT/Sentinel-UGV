"""추가 요구조자 위치 제보의 원문 보존·부정·불확실성 계약."""

from sentinel_voice.reported_person import (
    coerce_additional_person_reports,
    keyword_additional_person_reports,
)


def test_child_on_second_floor_is_unverified_unknown_response():
    reports = keyword_additional_person_reports("2층에 우리 아기가 있어요")

    assert reports == [
        {
            "subjectText": "우리 아기",
            "reportedCount": 1,
            "countStatus": "EXACT",
            "locationText": "2층",
            "reportedFloor": 2,
            "groundingStatus": "UNGROUNDED",
            "responseStatus": "UNKNOWN",
            "certaintyStatus": "ASSERTED",
            "rawUtterance": "2층에 우리 아기가 있어요",
            "verificationStatus": "UNVERIFIED",
            "operatorReviewRequired": True,
        }
    ]


def test_alone_and_explicit_absence_do_not_create_reports():
    assert keyword_additional_person_reports("저 혼자예요") == []
    assert keyword_additional_person_reports("2층에 아기는 없어요") == []


def test_unresponsive_person_is_not_counted_as_responsive_by_report():
    reports = keyword_additional_person_reports(
        "옆에 한 명 있는데 대답을 안 해요"
    )

    assert reports[0]["reportedCount"] == 1
    assert reports[0]["responseStatus"] == "UNRESPONSIVE"


def test_tentative_statement_preserves_uncertainty():
    reports = coerce_additional_person_reports(
        [
            {
                "subjectText": "누가",
                "reportedCount": None,
                "locationText": "2층",
                "responseStatus": "UNKNOWN",
                "certaintyStatus": "TENTATIVE",
            }
        ],
        "2층에 누가 있을지도 몰라요",
    )

    assert reports[0]["certaintyStatus"] == "TENTATIVE"
    assert reports[0]["reportedFloor"] == 2


def test_model_invented_location_is_removed():
    reports = coerce_additional_person_reports(
        [
            {
                "subjectText": "우리 아기",
                "reportedCount": 1,
                "locationText": "203호",
                "responseStatus": "UNKNOWN",
                "certaintyStatus": "ASSERTED",
            }
        ],
        "2층에 우리 아기가 있어요",
    )

    assert reports[0]["locationText"] is None
    assert reports[0]["reportedFloor"] == 2
    assert reports[0]["rawUtterance"] == "2층에 우리 아기가 있어요"


def test_landmark_only_report_preserves_presence_without_inventing_count():
    reports = keyword_additional_person_reports("저기 계단 옆에 있어요")

    assert reports == [
        {
            "subjectText": None,
            "reportedCount": None,
            "countStatus": "PRESENCE_CONFIRMED_COUNT_UNKNOWN",
            "locationText": "계단 옆",
            "reportedFloor": None,
            "groundingStatus": "UNGROUNDED",
            "responseStatus": "UNKNOWN",
            "certaintyStatus": "ASSERTED",
            "rawUtterance": "저기 계단 옆에 있어요",
            "verificationStatus": "UNVERIFIED",
            "operatorReviewRequired": True,
        }
    ]
