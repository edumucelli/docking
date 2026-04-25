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
