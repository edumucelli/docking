@native_services
Feature: Unit conversion preserves a finite result at floating point limits
  Scenario Outline: A finite conversion is displayed instead of infinity or zero
    Given Unit Converter is set to "<category>" from "<source>" to "<target>"
    When I enter the conversion value "<value>"
    Then the displayed conversion is finite and equals "<expected>"

    Examples:
      | category    | source | target | value  | expected               |
      | Length      | km     | km     | 1e308  | 1e308                  |
      | Length      | mm     | mm     | 5e-324 | 5e-324                 |
      | Temperature | F      | C      | 1e308  | 5.555555555555556e307  |
      | Temperature | C      | F      | 8e307  | 1.44e308               |
      | Temperature | F      | F      | 1e-308 | 1e-308                 |
