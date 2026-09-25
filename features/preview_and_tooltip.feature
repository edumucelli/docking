Feature: Preview and tooltip policy
  Hover feedback should remain coherent across preview handoff and autohide transitions.

  Scenario: Leaving the dock toward a visible preview delays autohide release
    Given preview support is enabled
    When I hover the running "firefox.desktop" dock item long enough for preview
    Then the preview for "firefox.desktop" is visible
    When I leave the dock while the preview is visible
    Then the preview hide is scheduled
    And the dock autohide leave is not released yet
    When the preview finishes hiding
    Then the dock autohide leave is released

  Scenario: Tooltips stay suppressed while the dock is still showing
    Given the dock is currently showing from autohide
    When I hover the "firefox.desktop" dock item
    Then the tooltip is suppressed

  Scenario Outline: Activating a preview does not prevent reopening the same icon
    Given preview interaction has autohide <state>
    When I hover the running "firefox.desktop" dock item long enough for preview
    And I leave the dock while the preview is visible
    And I enter the preview popup
    And I activate the previewed window
    Then the preview popup is hidden
    When I return to the "firefox.desktop" preview icon
    Then a preview show is pending
    When I advance dock time by 399 milliseconds
    Then the preview popup is hidden
    When I advance dock time by 1 milliseconds
    Then the preview for "firefox.desktop" is visible

    Examples:
      | state    |
      | disabled |
      | enabled  |

  Scenario Outline: Returning from a visible preview keeps its content open
    Given preview interaction has autohide <state>
    When I hover the running "firefox.desktop" dock item long enough for preview
    And I leave the dock while the preview is visible
    And I enter the preview popup
    And I leave the preview popup
    And I return to the "firefox.desktop" preview icon
    And I advance dock time by 1000 milliseconds
    Then the preview for "firefox.desktop" is visible
    And the preview has opened 1 times
    And the dock autohide leave is not released yet

    Examples:
      | state    |
      | disabled |
      | enabled  |

  Scenario: A preview dismissed by leaving can reopen on the same icon
    When I hover the running "firefox.desktop" dock item long enough for preview
    And I leave the dock while the preview is visible
    And I enter the preview popup
    And I leave the preview popup
    And I advance dock time by 300 milliseconds
    Then the preview popup is hidden
    When I hover the running "firefox.desktop" dock item long enough for preview
    Then the preview for "firefox.desktop" is visible

  Scenario: Switching icons during reentry shows only the new application's preview
    Given there are previewable windows for "code.desktop"
    When I hover the running "firefox.desktop" dock item long enough for preview
    And I leave the dock while the preview is visible
    And I activate the previewed window
    And I return to the "firefox.desktop" preview icon
    And I advance dock time by 200 milliseconds
    And I return to the "code.desktop" preview icon
    And I advance dock time by 399 milliseconds
    Then the preview popup is hidden
    When I advance dock time by 1 milliseconds
    Then the preview for "code.desktop" is visible
    And the preview has opened 2 times
