@native_services
Feature: Native backend services stay authoritative and responsive
  Compositor geometry and visibility must be known before the dock dodges.

  Scenario Outline: Hidden or unknown windows do not hide the dock
    Given a native overlapping window that is "<state>"
    When native window dodge is evaluated
    Then native dodge hides the dock "<hidden>"

    Examples:
      | state         | hidden |
      | visible       | yes    |
      | minimized     | no     |
      | off-workspace | no     |
      | unknown       | no     |

  Scenario: Native movement cannot restart the overlap evaluation indefinitely
    Given a native event-driven overlap monitor
    When 100 native changes arrive before dispatch
    Then one pending evaluation remains and shutdown cancels it

  Scenario: Standard toplevel listing does not pretend to manage windows
    Given a Wayland session with only standard toplevel listing
    Then the native fixture is listed but all management actions are unsupported

  Scenario Outline: Screenshot contracts remain bounded on a real private bus
    When a private compositor screenshot service responds "<response>"
    Then native preview is available "<available>" without a screen fallback

    Examples:
      | response     | available |
      | valid        | yes       |
      | denied       | no        |
      | timeout      | no        |
      | truncated    | no        |
      | wrong-window | no        |
