API_URL = "https://voltaplus.azurewebsites.net"
DAYS = ["Mon", "Tues", "Wed", "Thu", "Fri", "Sat", "Sun"];
CURR_DAY_OF_WEEK = (new Date().getDay() + 6) % 7;

let siteWatch = {
    'site': null,
    'station': null,
    'interval': null
};

function updateStates() {
    var buttons = Array();
    var sitesQuery = $.getJSON(`${API_URL}/sites`, function(json) {
        $.each(json, function(state, cities) {
            let button = $('<button>', {
                text: state.toUpperCase(),
                class: "list-group-item list-group-item-action list-item-button"
            }).click(function() {
                updateSelected($('#stateList'), $(this));
                updateCities(cities);
            });

            buttons.push(button);
        });
    });

    sitesQuery.done(function() {
        resetState(0);
        $('#stateList').append(...buttons);

        setDefault($('#stateList'));
        setUrlState();
    });
}

function updateCities(cities) {
    resetState(1);

    $.each(cities, function(city, sites) {
        $('#cityList').append($('<button>', {
            text: toTitleCase(city),
            class: "list-group-item list-group-item-action list-item-button"
        }).click(function() {
            updateSelected($('#cityList'), $(this));
            updateSites(sites);
        }));
    });

    setDefault($('#cityList'));
}

function updateSites(sites) {
    resetState(2);

    for (let site of sites.sort()) {
        $('#siteList').append($('<button>', {
            text: site[0],
            class: "list-group-item list-group-item-action list-item-button"
        }).click(function() {
            updateSelected($('#siteList'), $(this));
            updateStations(site[1]);
        }));
    }

    setDefault($('#siteList'));
}

function updateStations(stations) {
    var meterQueries = Array();
    var watchStatuses = Array();
    var buttons = Array();
    for (let [idx, station] of stations.entries()) {
        let meterQuery = $.getJSON(`${API_URL}/meters/${station.meters.join()}`, function(meters) {
            let button = $('<button>', {
                class: "list-group-item list-group-item-action list-item-button"
            }).click(function() {
                if ($('#watch').hasClass("active"))
                    siteWatch.station = idx;
                showMeters(station.name, meters);
            });

            if (siteWatch.station == idx && $('#meterModal').hasClass("show")) {
                $('#meterModal').modal("hide");
                button.click();
            }

            button.append($('<div>', {
                text: station.name
            }));
            for (let meter of meters) {
                let watchStatus = false;
                let status = $('<div>', {
                    style: "padding: 0 1em;"
                });
                status.append($('<span/>', {
                    class: "material-icons",
                    style: "vertical-align: text-top; padding-right:.25em;",
                    text: "ev_station"
                }));
                let statusClass = "text-";
                if (meter['availability'] == "available") {
                    statusClass += "secondary";
                }
                else {
                    if (meter['availability'] == "in use" || meter['availability'] == "plugged in...") {
                        statusClass += (meter['state'] == "chargestopped" || meter['charge_duration'] > 7200) ? "warning" : "primary";
                        status.append($('<span/>', {
                            text: durationFmt(meter['charge_duration'])
                        }));
                    }
                    else {
                        statusClass += "danger";
                    }
                    watchStatus = true;
                }
                status.addClass(statusClass);
                button.append(status);
                watchStatuses.push(watchStatus);
            }
            buttons.push(button);
        });

        meterQueries.push(meterQuery);
    }

    $.when(...meterQueries).done(function() {
        resetState(3, true);
        buttons.sort(function (a, b) {
            return ($(a).text() > $(b).text()) ? 1 : -1;
        });
        $('#stationList').append(...buttons);
        let enableWatch = watchStatuses.every(function (watchStatus) { return watchStatus; });
        let watchClass = enableWatch ? "btn btn-outline-primary" : "btn btn-outline-secondary";

        if (siteWatch.site != null) {
            if (siteWatch.site.is(getSelectedSite())) {
                if (!enableWatch) {
                    if ($('#watch').hasClass("active")) {
                        resetState();
                        alert("meter is now available");
                    }
                }
                else {
                    watchClass += " active";
                }
            }
            else {
                resetState();
            }
        }

        $('#watch').prop('disabled', !enableWatch);
        $('#watch').attr('class', watchClass);
    });
}

function toggleWatch(button) {
    if (button.hasClass("active")) {
        resetSiteWatch();
        button.removeClass("active");
    }
    else {
        siteWatch.site = getSelectedSite();
        if (siteWatch.site != null) {
            siteWatch.site.click();
            siteWatch.interval = setInterval(function() {
                if (siteWatch.site != null)
                    siteWatch.site.click();
                else
                    resetSiteWatch();
            }, 60000);
            button.addClass("active");
        }
    }
}

function resetSiteWatch() {
    siteWatch.site = null;
    siteWatch.station = null;
    if (siteWatch.interval != null) {
        clearInterval(siteWatch.interval);
        siteWatch.interval = null;
    }
}

function showMeters(stationName, data) {
    $('#stationName').text(stationName);
    $('#meters').empty();

    for (let meter in data) {
        let meterStats = $('<tr>');
        if (meter > 0) 
            meterStats.append($('<hr>'));
        let currProgressBar = $('<div>', {
            class: "progress-bar",
            width: ((data[meter]['charge_duration'] / 7200) * 100) + "%"
        });

        let showCurrCharge = false;
        if (data[meter]['availability'] == "in use" || data[meter]['availability'] == "plugged in...") {
            if (data[meter]['state'] == "chargestopped")
                currProgressBar.addClass("bg-warning");
            else if (data[meter]['charge_duration'] > 7200)
                currProgressBar.addClass("bg-warning progress-bar-striped progress-bar-animated");
            else
                currProgressBar.addClass("progress-bar-striped progress-bar-animated")
            showCurrCharge = true;
        }

        if (showCurrCharge) {
            meterStats.append($('<h5/>', {
                text: "current charge"
            }));
            let currCharge = $('<div>', {
                class: "row meter-data",
                style: "display: flex; align-items: center;"
            });
            let currProgress = $('<div>', {
                class: "progress"
            });
            currProgress.append(currProgressBar);
            currCharge.append($('<div>', {
                class: "col-md-8",
                style: "padding-left: 0;"
            }).append(currProgress));
            currCharge.append($('<span/>', {
                class: "col-md-4",
                text: durationFmt(data[meter]['charge_duration'])
            }));
            meterStats.append(currCharge);
        }

        meterStats.append($('<h5/>', {
            text: "average charge"
        }));
        meterStats.append($('<div/>', {
            text: durationFmt(data[meter]['in_use_charging_stats']['avg']),
            class: "meter-data"
        }));

        meterStats.append($('<h5/>', {
            text: "average 'squat'"
        }));
        meterStats.append($('<div/>', {
            text: durationFmt(data[meter]['in_use_stopped_stats']['avg']),
            class: "meter-data"
        }));

        meterStats.append($('<h5/>', {
            text: "popular times"
        }));
        let popularTimes = $('<div>', {
            class: "meter-data"
        });
        popularTimes.append(weeklyPagination(meter, data[meter]['weekly_usage']));
        popularTimes.append($('<div>', {
            id: "chart_" + meter
        }));
        meterStats.append(popularTimes);

        $('#meters').append(meterStats)
        $('#' + CURR_DAY_OF_WEEK + '_' + meter).click();
    }
    $('#meterModal').modal("show");
}

function weeklyPagination(idx, data) {
    var pagination = $('<ul>', {
        id: "pagination_" + idx,
        class: "pagination pagination-sm justify-content-center"
    });
    for (let day in DAYS) {
        let page = $('<li/>', {
            id: day + "_" + idx,
            class: "page-item"
        }).append($('<a/>', {
            class: "page-link",
            text: DAYS[day],
            href: "#"
        }));
        let chartData = Array();
        for (let i = 0; i < 144; i += 12) {
            let hour = i / 6;
            chartData.push([((hour % 12) + 1) + ((hour > 12) ? "pm" : "am"), data[day].slice(i, i + 12).reduce((a, b) => a + b, 0)]);
        }
        page.click(function(idx, data) {
            return function() {
                $('#pagination_' + idx).children("li").each(function() {
                    $(this).removeClass("active")
                });
                $(this).addClass("active");
                makeMeterChart(idx, data)
            }
        }(idx, chartData));
        pagination.append(page);
    }
    return pagination;
}

function makeMeterChart(idx, data) {
    $('#chart_' + idx).empty();
    var chart = anychart.column();
    chart.tooltip(false);
    var dataSet = anychart.data.set(data);
    var mapping = dataSet.mapAs({
        x: 0,
        value: 1
    });
    chart.yAxis().enabled(false);
    chart.xAxis().ticks().enabled(false);
    var series = chart.column(mapping);
    series.normal().fill("#007bff");
    series.normal().stroke(null);
    series.hovered().fill("#007bff");
    series.hovered().stroke(null);
    series.selected().fill("#007bff");
    series.selected().stroke(null);

    chart.container("chart_" + idx);
    chart.draw();
}

function toTitleCase(str) {
    return str.split(' ').map((s) => s.charAt(0).toUpperCase() + s.substring(1)).join(' ');
};

function resetState(level=-1, skipWatch=false) {
    var lists = [$('#stateList'), $('#cityList'), $('#siteList'), $('#stationList')];
    if (level >= 0) {
        for (let i = level; i < lists.length; i++) {
            lists[i].empty();
            lists[i].scrollTop();
        }
    }

    if (!skipWatch) {
        resetSiteWatch();
        $('#watch').prop('disabled', true);
        $('#watch').attr('class', "btn btn-outline-secondary");
    }
}

function updateSelected(list, selected) {
    list.children("button").each(function() {
        $(this).removeClass("active")
    });
    selected.addClass("active");
}

function setDefault(list) {
    var buttons = list.children("button");
    if (buttons.length == 1)
        buttons[0].click();
}

function getSelectedSite() {
    var siteButton = null;
    $('#siteList').children("button").each(function() {
        if ($(this).hasClass("active")) {
            siteButton = $(this);
            return false;
        }
    });

    return siteButton;
}

function setUrlState() {
    (new URL(document.location)).searchParams.forEach(function(val, key) {
        let buttons = $(`#${key}List`).children("button");
        if (buttons.length > 1) {
            for (let button of buttons) {
                if ($(button).text().toLowerCase() == val.toLowerCase()) {
                    button.click();
                    break;
                }
            }
        }
    });
}

function durationFmt(totalSeconds) {
    var hrs = Math.floor(totalSeconds / 3600);
    var mins = Math.floor(totalSeconds / 60) % 60;
    var secs = totalSeconds % 60;

    if (hrs == 0 && mins == 0)
        return `${secs}sec`;

    var durations = Array();
    if (hrs > 0)
        durations.push(`${hrs}hr`);
    if (mins > 0)
        durations.push(`${mins}min`);

    return durations.join(' ')
}
