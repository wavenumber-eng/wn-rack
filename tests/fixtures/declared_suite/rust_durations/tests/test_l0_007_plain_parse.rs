//! L0_007 (Rust): parse_duration against the same cases as
//! L0_units/vectors/L0_001_parse_duration.json. The fixture crate has no JSON
//! dependency, so the cases are written out here.

use rust_durations::parse_duration;

#[test]
fn l0_007_plain_parse() {
    assert_eq!(parse_duration("1h30m"), 5400);
    assert_eq!(parse_duration("1.5h"), 5400);
    assert_eq!(parse_duration("2h3m4s"), 7384);
}
