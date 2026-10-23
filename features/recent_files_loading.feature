@native_services
Feature: Avoid invisible and older Recent Files work
  Scenario: Visible thumbnails do not truncate the full recent menu
    Given a recent-files applet with 15 available history records
    When its recent stack is opened
    Then only nine recent-file icons are resolved
    And all 15 recent files remain available in its menu
    When the last recent menu entry is activated
    Then the last retained recent file is opened

  Scenario: Large history refresh stops after 15 valid files
    Given a recent-files applet with 1000 available history records
    When its recent history changes
    Then the newest 15 files are selected with 15 filesystem checks
    And the recent-file change is published once

  Scenario: Missing new files do not displace older existing files
    Given a recent-files applet with 100 available history records
    And its seven newest recent files no longer exist
    When its recent history changes
    Then the newest 15 existing files are selected with 22 filesystem checks

  Scenario: Short histories keep all surviving files
    Given a recent-files applet with 8 available history records
    When its recent history changes
    Then all 8 recent files are selected with 8 filesystem checks
