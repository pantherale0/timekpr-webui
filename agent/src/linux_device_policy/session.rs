use zbus::zvariant::OwnedObjectPath;
use zbus::{Connection, Proxy};

pub const PRIMARY_SEAT: &str = "seat0";

pub async fn query_primary_seat_active_username(
    connection: &Connection,
) -> Result<Option<String>, String> {
    let seat_path = format!("/org/freedesktop/login1/seat/{PRIMARY_SEAT}");
    let seat_proxy = Proxy::new(
        connection,
        "org.freedesktop.login1",
        seat_path.as_str(),
        "org.freedesktop.login1.Seat",
    )
    .await
    .map_err(|error| format!("failed to create logind seat proxy: {error}"))?;

    let active_session: (String, OwnedObjectPath) = seat_proxy
        .get_property("ActiveSession")
        .await
        .map_err(|error| format!("failed to read ActiveSession for {PRIMARY_SEAT}: {error}"))?;

    if active_session.0.trim().is_empty() {
        return Ok(None);
    }

    let session_proxy = Proxy::new(
        connection,
        "org.freedesktop.login1",
        active_session.1.as_str(),
        "org.freedesktop.login1.Session",
    )
    .await
    .map_err(|error| format!("failed to create logind session proxy: {error}"))?;

    let session_class: String = session_proxy
        .get_property("Class")
        .await
        .map_err(|error| format!("failed to read session Class: {error}"))?;
    if !session_class.starts_with("user") {
        return Ok(None);
    }

    let username: String = session_proxy
        .get_property("Name")
        .await
        .map_err(|error| format!("failed to read session Name: {error}"))?;
    let normalized = username.trim().to_string();
    if normalized.is_empty() {
        Ok(None)
    } else {
        Ok(Some(normalized))
    }
}
