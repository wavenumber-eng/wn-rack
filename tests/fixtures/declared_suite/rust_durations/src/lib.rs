//! A fixture Rust implementation of duration parsing for Rack's own tests.

/// Whole seconds in a string such as `1h30m`.
pub fn parse_duration(text: &str) -> u64 {
    let mut total = 0.0_f64;
    let mut number = String::new();
    for character in text.chars() {
        let unit = match character {
            'h' => 3600.0,
            'm' => 60.0,
            's' => 1.0,
            _ => {
                number.push(character);
                continue;
            }
        };
        total += number.parse::<f64>().unwrap_or(0.0) * unit;
        number.clear();
    }
    total.round() as u64
}
