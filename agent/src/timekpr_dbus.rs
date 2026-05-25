use serde_json::{Map as JsonMap, Value as JsonValue};
use std::collections::HashMap;
use users::get_user_by_name;
use zbus::zvariant::OwnedValue;
use zbus::{Connection, Proxy};

const TIMEKPR_DBUS_BUS_NAME: &str = "com.timekpr.server";
const TIMEKPR_DBUS_SERVER_PATH: &str = "/com/timekpr/server";
const TIMEKPR_DBUS_USER_ADMIN_INTERFACE: &str = "com.timekpr.server.user.admin";
const TIMEKPR_INFO_LEVEL_FULL: &str = "F";

pub type TimekprConfig = JsonMap<String, JsonValue>;
pub type AllowedHourSpec = HashMap<String, i32>;
pub type AllowedHoursDay = HashMap<String, AllowedHourSpec>;

pub struct TimekprDbusClient {
    connection: Connection,
}

impl TimekprDbusClient {
    pub async fn connect() -> Result<Self, String> {
        let connection = Connection::system()
            .await
            .map_err(|e| format!("Failed to connect to the TimeKpr system bus: {e}"))?;
        Ok(Self { connection })
    }

    async fn user_admin_proxy(&self) -> Result<Proxy<'_>, String> {
        Proxy::new(
            &self.connection,
            TIMEKPR_DBUS_BUS_NAME,
            TIMEKPR_DBUS_SERVER_PATH,
            TIMEKPR_DBUS_USER_ADMIN_INTERFACE,
        )
        .await
        .map_err(|e| format!("Failed to create the TimeKpr user admin proxy: {e}"))
    }

    pub async fn get_user_information(
        &self,
        username: &str,
    ) -> Result<(i32, String, TimekprConfig), String> {
        let proxy = self.user_admin_proxy().await?;
        let (result, message, values): (i32, String, HashMap<String, OwnedValue>) = proxy
            .call("getUserInformation", &(username, TIMEKPR_INFO_LEVEL_FULL))
            .await
            .map_err(|e| format!("Failed to query TimeKpr user information: {e}"))?;

        let mut config = JsonMap::new();
        for (key, value) in values {
            config.insert(key, owned_value_to_json(value));
        }

        if let Some(uid) = lookup_linux_uid(username) {
            config.insert("LINUX_UID".to_string(), JsonValue::from(uid));
        }

        Ok((result, message, config))
    }

    pub async fn set_time_left(
        &self,
        username: &str,
        operation: &str,
        seconds: i32,
    ) -> Result<(i32, String), String> {
        let proxy = self.user_admin_proxy().await?;
        proxy.call("setTimeLeft", &(username, operation, seconds))
            .await
            .map_err(|e| format!("Failed to update time left through TimeKpr D-Bus: {e}"))
    }

    pub async fn set_allowed_days(
        &self,
        username: &str,
        allowed_days: &[String],
    ) -> Result<(i32, String), String> {
        let proxy = self.user_admin_proxy().await?;
        proxy.call("setAllowedDays", &(username, allowed_days))
            .await
            .map_err(|e| format!("Failed to update allowed days through TimeKpr D-Bus: {e}"))
    }

    pub async fn set_time_limit_for_days(
        &self,
        username: &str,
        day_limits: &[i32],
    ) -> Result<(i32, String), String> {
        let proxy = self.user_admin_proxy().await?;
        proxy.call("setTimeLimitForDays", &(username, day_limits))
            .await
            .map_err(|e| format!("Failed to update daily time limits through TimeKpr D-Bus: {e}"))
    }

    pub async fn set_allowed_hours(
        &self,
        username: &str,
        day_number: &str,
        hour_list: &AllowedHoursDay,
    ) -> Result<(i32, String), String> {
        let proxy = self.user_admin_proxy().await?;
        proxy
            .call("setAllowedHours", &(username, day_number, hour_list))
            .await
            .map_err(|e| format!("Failed to update allowed hours through TimeKpr D-Bus: {e}"))
    }
}

fn lookup_linux_uid(username: &str) -> Option<u32> {
    get_user_by_name(username).map(|user| user.uid())
}

fn owned_value_to_json(value: OwnedValue) -> JsonValue {
    if let Ok(map) = HashMap::<String, HashMap<String, i32>>::try_from(value.clone()) {
        return JsonValue::Object(
            map.into_iter()
                .map(|(outer_key, inner_map)| {
                    let inner_json = JsonValue::Object(
                        inner_map
                            .into_iter()
                            .map(|(inner_key, inner_value)| (inner_key, JsonValue::from(inner_value)))
                            .collect(),
                    );
                    (outer_key, inner_json)
                })
                .collect(),
        );
    }

    if let Ok(map) = HashMap::<String, OwnedValue>::try_from(value.clone()) {
        return JsonValue::Object(
            map.into_iter()
                .map(|(key, inner)| (key, owned_value_to_json(inner)))
                .collect(),
        );
    }

    if let Ok(items) = Vec::<Vec<String>>::try_from(value.clone()) {
        return JsonValue::Array(
            items
                .into_iter()
                .map(|inner| JsonValue::Array(inner.into_iter().map(JsonValue::String).collect()))
                .collect(),
        );
    }

    if let Ok(items) = Vec::<String>::try_from(value.clone()) {
        return JsonValue::Array(items.into_iter().map(JsonValue::String).collect());
    }

    if let Ok(items) = Vec::<i32>::try_from(value.clone()) {
        return JsonValue::Array(items.into_iter().map(JsonValue::from).collect());
    }

    if let Ok(boolean_value) = bool::try_from(value.clone()) {
        return JsonValue::Bool(boolean_value);
    }

    if let Ok(string_value) = String::try_from(value.clone()) {
        return JsonValue::String(string_value);
    }

    if let Ok(int_value) = i64::try_from(value.clone()) {
        return JsonValue::from(int_value);
    }

    if let Ok(int_value) = i32::try_from(value.clone()) {
        return JsonValue::from(int_value);
    }

    if let Ok(int_value) = u64::try_from(value.clone()) {
        return JsonValue::from(int_value);
    }

    if let Ok(int_value) = u32::try_from(value.clone()) {
        return JsonValue::from(int_value);
    }

    serde_json::to_value(value).unwrap_or(JsonValue::Null)
}
