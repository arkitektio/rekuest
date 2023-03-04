from rekuest.messages import (
    Assignation,
    AssignationStatus,
)


def test_update_message():
    x = Assignation(
        assignation=1, status=AssignationStatus.PENDING, args=[1], guardian=1
    )
    y = Assignation(
        assignation=1,
        status=AssignationStatus.RETURNED,
        args=["nana"],
        kwargs=None,
        guardian=1,
    )

    x.update(y)
    assert x.status == AssignationStatus.RETURNED, "Status should be updated"
    assert x.args == ["nana"], "Args should have been updated"


def test_update_message_not_inplace():
    x = Assignation(
        assignation=1, status=AssignationStatus.PENDING, args=[0], guardian=1
    )
    y = Assignation(
        assignation=1,
        status=AssignationStatus.RETURNED,
        args=["nana"],
        guardian=1,
    )

    t = x.update(use=y, in_place=False)
    assert x.status == AssignationStatus.PENDING, "Status should have not been updated"
    assert x.args == [0], "Kwargs should have not been updated"

    assert (
        t.status == AssignationStatus.RETURNED
    ), "Status of copy should have been updated"
    assert t.args == ["nana"], "Kwargs  of copy should have not been updated"
